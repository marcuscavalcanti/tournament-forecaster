SHELL := /bin/zsh

PYTHON ?= uv run python
PYTEST ?= uv run --with pytest --with jsonschema python -m pytest
PYTHON_WITH_PILLOW ?= uv run --with pillow python

CONFIG ?= config/worldcup_brazil.json
STATE ?= data/run_state.json
SOURCE_MEMORY ?= data/source_memory.json
OUTPUT_DIR ?= outputs
WATCHDOG_LOG ?= data/watchdog.jsonl
CALIBRATION_INPUT ?= data/calibration_predictions.json
CALIBRATION_MIN_RESOLVED ?= 1
RESULTS_SOURCE ?= fifa
RESULTS_INPUT ?=
FIFA_RESULTS_URL ?= https://api.fifa.com/api/v3/calendar/matches
MARKET_ODDS_SOURCE ?= the-odds-api
MARKET_ODDS_INPUT ?=
MARKET_ODDS_URL ?= https://api.the-odds-api.com/v4/sports/soccer_fifa_world_cup_winner/odds/?regions=us,uk,eu&markets=outrights&oddsFormat=decimal&apiKey={THE_ODDS_API_KEY}
MARKET_ODDS_REQUIRED ?= 0
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

UPDATE_RESULTS_ARGS := --config "$(CONFIG)"
ifneq ($(strip $(RESULTS_INPUT)),)
UPDATE_RESULTS_ARGS += --results "$(RESULTS_INPUT)"
else ifeq ($(RESULTS_SOURCE),fifa)
UPDATE_RESULTS_ARGS += --from-fifa --fifa-url "$(FIFA_RESULTS_URL)"
endif
ifeq ($(APPLY),1)
UPDATE_RESULTS_ARGS += --apply
endif

UPDATE_MARKET_ODDS_ARGS := --config "$(CONFIG)"
ifneq ($(strip $(MARKET_ODDS_INPUT)),)
UPDATE_MARKET_ODDS_ARGS += --odds-json "$(MARKET_ODDS_INPUT)"
else ifeq ($(MARKET_ODDS_SOURCE),the-odds-api)
UPDATE_MARKET_ODDS_ARGS += --from-the-odds-api --odds-url '$(MARKET_ODDS_URL)'
endif
ifeq ($(APPLY),1)
UPDATE_MARKET_ODDS_ARGS += --apply
endif
ifeq ($(MARKET_ODDS_REQUIRED),1)
UPDATE_MARKET_ODDS_ARGS += --require
endif

.PHONY: help quickstart daily force watch doctor diagrams calibration profile validate debate update-results update-market-odds calibrate-rho calibrate-base-rating

help:
	@printf "Main commands:\n"
	@printf "  make daily      runs the legacy daily job; creates a post only after 3 days\n"
	@printf "  make force      runs the legacy daily job now, ignoring the 3-day window\n"
	@printf "  make watch      follows the watchdog log in real time\n"
	@printf "  make debate     renders the legacy opponent room and Brazil room\n"
	@printf "  make doctor     diagnoses legacy agent quorum and sources without a post\n"
	@printf "  make diagrams   regenerates the legacy engine diagram PNGs\n"
	@printf "  make calibration validates Brier, log loss, and ECE from CALIBRATION_INPUT\n"
	@printf "  make profile    reports elapsed time by stage and round for the latest run\n"
	@printf "  make update-results fetches FIFA scores; APPLY=1 writes, RESULTS_INPUT imports a local file\n"
	@printf "  make update-market-odds fetches outright odds; APPLY=1 writes market_outright_odds\n"
	@printf "  make validate   runs tests, compileall, and example JSON validation\n"
	@printf "  make quickstart generates a complete synthetic offline forecast\n"

quickstart:
	$(PYTHON) -m tournament_forecaster quickstart

daily:
	@mkdir -p data "$(OUTPUT_DIR)"
	$(MAKE) update-results APPLY=1
	$(MAKE) update-market-odds APPLY=1
	$(RUN_DAILY)

force:
	@mkdir -p data "$(OUTPUT_DIR)"
	$(MAKE) update-results APPLY=1
	$(MAKE) update-market-odds APPLY=1
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
	$(PYTHON) scripts/validate_calibration.py --input "$(CALIBRATION_INPUT)" --min-resolved "$(CALIBRATION_MIN_RESOLVED)"

profile:
	$(PYTHON) scripts/profile_run.py --watchdog-log "$(WATCHDOG_LOG)" $(PROFILE_ARGS)

update-results:
	$(PYTHON) scripts/update_group_results.py $(UPDATE_RESULTS_ARGS)

update-market-odds:
	$(PYTHON) scripts/update_market_odds.py $(UPDATE_MARKET_ODDS_ARGS)

calibrate-rho:
	$(MAKE) update-market-odds APPLY=1
	$(PYTHON) scripts/calibrate_rho.py $(CALIBRATE_ARGS)

calibrate-base-rating:
	$(PYTHON) scripts/calibrate_base_rating.py $(CALIBRATE_ARGS)

validate:
	$(PYTEST) -q
	$(PYTHON) -m compileall -q worldcup_brazil scripts
	$(PYTHON) scripts/validate_blind_peer_review_contract.py >/tmp/worldcup_blind_peer_review_contract.json
	$(PYTHON) scripts/validate_opponent_room_contract.py >/tmp/worldcup_opponent_room_contract.json
	python3 -m json.tool config/worldcup_brazil.example.json >/tmp/worldcup_brazil_config_check.json
