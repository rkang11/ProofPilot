.PHONY: dev test check eval assistant-eval repair-eval reset

PORT ?= 8000

dev:
	PROOFPILOT_PORT=$(PORT) python3 backend/server.py

test:
	python3 -m unittest discover

check:
	python3 -m py_compile backend/server.py backend/assist.py backend/db.py backend/env.py backend/repair.py backend/repair_service.py backend/static.py backend/llm.py worker/verify.py evals/run_benchmarks.py evals/run_assistant_benchmarks.py evals/run_repair_benchmarks.py
	node --check frontend/app.js

eval:
	python3 evals/run_benchmarks.py

assistant-eval:
	python3 evals/run_assistant_benchmarks.py

repair-eval:
	python3 evals/run_repair_benchmarks.py

reset:
	curl -X POST http://127.0.0.1:$(PORT)/api/dev/reset
