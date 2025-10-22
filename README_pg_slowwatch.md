# Как использовать

1. Сохрани файл, сделай исполняемым:

chmod +x /opt/pg_slowwatch/pg_slowwatch.py


2. Запусти (пример с node_exporter textfile_collector):

/opt/pg_slowwatch/pg_slowwatch.py \
  --log /var/log/postgresql/postgresql-15-main.log \
  --metrics /var/lib/node_exporter/textfile_collector/slowqueries.prom \
  --threshold-ms 500 \
  --labels env=prod,instance=db01


3. Убедись, что node_exporter собран с --collector.textfile.directory=/var/lib/node_exporter/textfile_collector (или укажи свою директорию).

4. В Prometheus появятся метрики:

pg_slow_queries_total{env="prod",instance="db01",user="appuser",db="maindb"}

pg_slow_queries_ms_sum{...}

	Скрипт ориентируется на строки вида duration: 1234.567 ms — это стандартный вывод log_min_duration_statement. Убедись в postgresql.conf:
log_min_duration_statement = 500 (или ниже, если хочешь видеть всё и фильтровать порогом в скрипте).

	Для парсинга user@db скрипт полагается на префикс в логе (log_line_prefix), например:
log_line_prefix = '%m [%p] %u@%d %r ' — это рекомендуемо.

	Запись метрик атомарная — безопасно для node_exporter.

	Защита от ротации: определяется смена inode/укорочение файла, после чего файл переоткрывается.