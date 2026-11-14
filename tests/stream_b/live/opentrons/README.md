# Opentrons simulator smoke protocols (stream C's tool, installed here for the team)

`opentrons_simulate` 7.5.0 lives in `.venvs/opentrons` (`uv venv --python 3.10 .venvs/opentrons && uv pip install --python .venvs/opentrons/bin/python opentrons==7.5.0`).

    .venvs/opentrons/bin/opentrons_simulate tests/stream_b/live/opentrons/ok_antigen_stimulation.py     # prints the pipetting steps, exit 0
    .venvs/opentrons/bin/opentrons_simulate tests/stream_b/live/opentrons/bad_volume_too_large.py       # ProtocolCommandFailedError: Cannot aspirate 500.0 uL when only 300.0 is available

Run 2026-11-14: the good protocol simulates; the bad one is rejected with a precise error. That is the behaviour the S9 validator layer relies on
(README: "simulation passing is not correctness", but simulation *failing* is a reliable red flag).
