#!/usr/bin/env bash
# Check SSL/TLS certificate expiration for host:port targets.
# Exit codes: 0 OK, 1 WARNING, 2 CRITICAL, 3 UNKNOWN

set -euo pipefail

# Defaults
WARN_DAYS=${WARN_DAYS:-30}
CRIT_DAYS=${CRIT_DAYS:-7}
TIMEOUT=${TIMEOUT:-10}
OPENSSL_BIN=${OPENSSL_BIN:-openssl}
DATE_BIN=${DATE_BIN:-date}   # GNU date

usage() {
  cat <<EOF
Usage:
  $0 host[:port] [host2[:port] ...]
  $0 -f targets.txt

Env:
  WARN_DAYS (default: ${WARN_DAYS})
  CRIT_DAYS (default: ${CRIT_DAYS})
  TIMEOUT   (default: ${TIMEOUT}s)

Examples:
  $0 example.com
  $0 example.com:8443 api.example.com
  WARN_DAYS=20 CRIT_DAYS=5 $0 -f targets.txt
EOF
}

# Colored output (disable with NO_COLOR=1)
c_ok=$'\e[32m'; c_warn=$'\e[33m'; c_crit=$'\e[31m'; c_dim=$'\e[90m'; c_rst=$'\e[0m'
[[ "${NO_COLOR:-}" == "1" ]] && c_ok="" && c_warn="" && c_crit="" && c_dim="" && c_rst=""

TARGETS=()

# Parse args
if [[ $# -eq 0 ]]; then usage; exit 3; fi
while [[ $# -gt 0 ]]; do
  case "$1" in
    -f|--file)
      [[ -n "${2:-}" ]] || { echo "ERROR: -f requires a file" >&2; exit 3; }
      mapfile -t file_targets < <(grep -vE '^\s*#' "$2" | awk 'NF')
      TARGETS+=("${file_targets[@]}")
      shift 2
      ;;
    -h|--help)
      usage; exit 0;;
    *)
      TARGETS+=("$1"); shift;;
  esac
done

# Helper: fetch notAfter epoch seconds and some info
fetch_cert_info() {
  local host="$1" port="$2"

  # SNI: -servername host ; timeout via stdbuf+timeout
  local out
  if ! out=$(timeout "${TIMEOUT}" "${OPENSSL_BIN}" s_client \
          -servername "${host}" -connect "${host}:${port}" -showcerts </dev/null 2>/dev/null \
          | "${OPENSSL_BIN}" x509 -noout -enddate -issuer -subject 2>/dev/null); then
    echo "__ERROR__ Unable to connect or parse cert"
    return 1
  fi

  local not_after issuer subject
  not_after=$(sed -n 's/^notAfter=//p' <<<"$out")
  issuer=$(sed -n 's/^issuer= //p' <<<"$out")
  subject=$(sed -n 's/^subject= //p' <<<"$out")

  # Convert to epoch (GNU date)
  local end_epoch
  if ! end_epoch=$("${DATE_BIN}" -u -d "${not_after}" +%s 2>/dev/null); then
    echo "__ERROR__ Failed to parse notAfter: ${not_after}"
    return 1
  fi

  echo "${end_epoch}|${issuer}|${subject}|${not_after}"
  return 0
}

now_epoch=$("${DATE_BIN}" -u +%s)
overall_rc=0
printf "%s\n" "${c_dim}WARN_DAYS=${WARN_DAYS}, CRIT_DAYS=${CRIT_DAYS}, TIMEOUT=${TIMEOUT}s${c_rst}"

for item in "${TARGETS[@]}"; do
  host="${item%:*}"
  port="${item#*:}"
  [[ "${host}" == "${port}" ]] && port="443"  # no colon -> default 443

  info=$(fetch_cert_info "${host}" "${port}" || true)
  if [[ "$info" == __ERROR__* ]]; then
    echo -e "${c_crit}CRITICAL${c_rst} ${host}:${port} — ${info#__ERROR__ }"
    overall_rc=$(( overall_rc < 2 ? 2 : overall_rc ))
    continue
  fi

  IFS='|' read -r end_epoch issuer subject not_after <<<"$info"
  days_left=$(( (end_epoch - now_epoch) / 86400 ))

  # Classify
  status="OK"; color="$c_ok"; rc=0
  if (( days_left < 0 )); then
    status="CRITICAL"; color="$c_crit"; rc=2
  elif (( days_left <= CRIT_DAYS )); then
    status="CRITICAL"; color="$c_crit"; rc=2
  elif (( days_left <= WARN_DAYS )); then
    status="WARNING"; color="$c_warn"; rc=1
  fi

  # Output line
  printf "%b%s%b %s:%s — %d days left (notAfter: %s)\n" \
         "$color" "$status" "$c_rst" "$host" "$port" "$days_left" "$not_after"

  # Optional verbose details (set VERBOSE=1)
  if [[ "${VERBOSE:-0}" == "1" ]]; then
    printf "  issuer:  %s\n  subject: %s\n" "$issuer" "$subject"
  fi

  # Aggregate exit code (max severity)
  if (( rc > overall_rc )); then overall_rc=$rc; fi
done

exit $overall_rc
