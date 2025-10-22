# Примеры использования

# Один хост, порт по умолчанию 443
./check_ssl.sh example.com

# Несколько целей, кастомный порт
./check_ssl.sh api.example.com:8443 grafana.example.com

# Задать пороги через env
WARN_DAYS=20 CRIT_DAYS=5 ./check_ssl.sh example.com

# Список целей из файла (формат: по одному host[:port] на строку, # — комментарии)
./check_ssl.sh -f check_ssl_targets.txt

# Подробный вывод с issuer/subject
VERBOSE=1 ./check_ssl.sh example.com


# Exit-коды: 0 OK, 1 WARNING, 2 CRITICAL, 3 UNKNOWN — удобно для Nagios/Icinga/Prometheus-blackbox wrapper’ов и CI.

#Cron (ежедневная проверка + лог) (crontab -e)
0 8 * * * /opt/ssl/check_ssl.sh -f /opt/ssl/check_ssl_targets.txt >> /var/log/check_ssl.log 2>&1

# Скрипт использует SNI (-servername host) — корректно проверяет мульти-SAN сертификаты за балансировщиком.

# Если где-то терминируется TLS (AWS ELB/Cloudflare) — проверяешь именно точку терминации (FQDN/порт балансировщика).

# Для внутренних сервисов с self-signed — всё равно парсится notAfter (нам не важно, валидна цепочка или нет, только срок).