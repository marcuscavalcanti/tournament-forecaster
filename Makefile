SHELL := /bin/zsh

PYTHON ?= uv run python
PYTEST ?= uv run --with pytest python -m pytest
PYTHON_WITH_PILLOW ?= uv run --with pillow python

CONFIG ?= config/worldcup_brazil.json
STATE ?= data/run_state.json
SOURCE_MEMORY ?= data/source_memory.json
OUTPUT_DIR ?= outputs
WATCHDOG_LOG ?= data/watchdog.jsonl
CALIBRATION_INPUT ?= data/calibration_predictions.json
DEBATE_INPUT ?=
DEBATE_OUTPUT ?=

RUN_DAILY := $(PYTHON) scripts/run_daily_worldcup_brazil.py \
	--config "$(CONFIG)" \
	--state "$(STATE)" \
	--source-memory "$(SOURCE_MEMORY)" \
	--output-dir "$(OUTPUT_DIR)" \
	--watchdog-log "$(WATCHDOG_LOG)" \
	--calibration-log "$(CALIBRATION_INPUT)"

DEBATE_ARGS := --output-dir "$(OUTPUT_DIR)" --watchdog-log "$(WATCHDOG_LOG)"
ifneq ($(strip $(DEBATE_INPUT)),)
DEBATE_ARGS += --input "$(DEBATE_INPUT)"
endif
ifneq ($(strip $(DEBATE_OUTPUT)),)
DEBATE_ARGS += --output "$(DEBATE_OUTPUT)"
endif

.PHONY: help daily force watch doctor diagrams calibration profile validate debate

help:
	@printf "Comandos principais:\n"
	@printf "  make daily      roda o job diário; só gera novo post se passaram 3 dias\n"
	@printf "  make force      força um run agora, ignorando a janela de 3 dias\n"
	@printf "  make watch      acompanha o watchdog em tempo real\n"
	@printf "  make debate     mostra a sala adversários -> sala Brasil de forma estruturada\n"
	@printf "  make doctor     diagnostica quorum/fontes dos agentes sem renderizar post\n"
	@printf "  make diagrams   regenera os PNGs dos diagramas da engine\n"
	@printf "  make calibration valida Brier/log loss/ECE usando CALIBRATION_INPUT\n"
	@printf "  make profile    breakdown de tempo por etapa/rodada do último run no watchdog\n"
	@printf "  make validate   roda testes, compileall e valida o JSON exemplo\n"

daily:
	@mkdir -p data "$(OUTPUT_DIR)"
	$(RUN_DAILY)

force:
	@mkdir -p data "$(OUTPUT_DIR)"
	$(RUN_DAILY) --force

watch:
	@mkdir -p data
	@touch "$(WATCHDOG_LOG)"
	tail -f "$(WATCHDOG_LOG)"

debate:
	@mkdir -p "$(OUTPUT_DIR)"
	$(PYTHON) scripts/render_debate_report.py $(DEBATE_ARGS)

doctor:
	@mkdir -p "$(OUTPUT_DIR)"
	$(PYTHON) scripts/run_agent_source_harness.py --config "$(CONFIG)"

diagrams:
	@mkdir -p "$(OUTPUT_DIR)"
	$(PYTHON_WITH_PILLOW) scripts/generate_framework_diagram_pngs.py

calibration:
	$(PYTHON) scripts/validate_calibration.py --input "$(CALIBRATION_INPUT)"

profile:
	$(PYTHON) scripts/profile_run.py --watchdog-log "$(WATCHDOG_LOG)" $(PROFILE_ARGS)

validate:
	$(PYTEST) -q
	$(PYTHON) -m compileall -q worldcup_brazil scripts
	$(PYTHON) scripts/validate_blind_peer_review_contract.py >/tmp/worldcup_blind_peer_review_contract.json
	python3 -m json.tool config/worldcup_brazil.example.json >/tmp/worldcup_brazil_config_check.json
