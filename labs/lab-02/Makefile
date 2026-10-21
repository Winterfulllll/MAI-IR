# Поисковый робот. Все цели запускаются из каталога lab-02.

CONFIG ?= config.yaml
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
MONGO_URI ?= mongodb://localhost:27017

.PHONY: venv db-up db-down db-shell crawl recrawl reparse validate stats test test-mongo test-report report clean

venv:                   ## окружение с зависимостями
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt

db-up:                  ## поднять MongoDB в докере и дождаться готовности
	docker compose up -d --wait

db-down:                ## остановить MongoDB (данные остаются в томе mongo-data)
	docker compose down

db-shell:               ## консоль mongosh в базе робота
	docker compose exec mongo mongosh crawler

crawl:                  ## обкачка (Ctrl+C -- мягкая остановка)
	$(PYTHON) -u src/crawler.py $(CONFIG)

recrawl:                ## проверка изменений и постановка в очередь
	$(PYTHON) -u src/recrawler.py $(CONFIG)

validate:               ## проверка базы; LIVE=5 -- ещё перекачать 5 документов и сравнить
	$(PYTHON) -u src/validate.py $(CONFIG) --live $(or $(LIVE),0)

reparse:                ## заново выделить текст из сохранённого raw_html (без перекачки)
	$(PYTHON) -u src/reparse.py $(CONFIG)

stats:                  ## сводка по базе
	$(PYTHON) src/stats.py $(CONFIG)

test:                   ## автотесты на MongoDB в памяти (mongomock), без сети и докера
	$(PYTHON) -m unittest discover -s tests -t . -v

test-mongo:             ## те же тесты на настоящей MongoDB (нужен make db-up)
	MONGO_URI=$(MONGO_URI) $(PYTHON) -m unittest discover -s tests -t . -v

test-report:            ## результат автотестов на настоящей MongoDB -- для отчёта
	MONGO_URI=$(MONGO_URI) $(PYTHON) -m unittest discover -s tests -t . -v 2>&1 | tee report/tests.log

report:                 ## сборка отчёта
	$(MAKE) -C report pdf

clean:                  ## временные файлы Python и LaTeX (report.pdf остаётся)
	rm -rf src/__pycache__ tests/__pycache__
	rm -f report/report.aux report/report.log report/report.out report/report.toc
