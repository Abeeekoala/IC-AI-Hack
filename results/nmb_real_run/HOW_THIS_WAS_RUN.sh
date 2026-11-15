set -e
cd <repo root>
T=/private/tmp/vf_tools; E=docs/examples
rm -rf runs/real_fixture; cp -R fixtures/nmb_fixture runs/real_fixture
export PATH=$T/.envs/blast/bin:$T/.venv/bin:$PATH
.venv/bin/python -m vaxforge run --out runs/real_fixture --stages s3,s4,s5,s6,s7,s8,s9,s9plus,s9_5,s10,s11,s12 --offline --non-interactive \
  --cache-dir $T/cache \
  --set s3.backend=scores_file --set "s3.scores_file=\"$E/vaxign_ml_scores.nmb_fixture.tsv\"" \
  --set "tools.algpred2=\"$T/.venvs/algpred2/bin/algpred2\"" --set "tools.toxinpred2=\"$T/.venv/bin/toxinpred2\"" \
  --set "tools.bepipred3=\"$T/.venvs/bepipred/bin/python $T/.tools/BepiPred-3.0/bepipred3_CLI.py\"" \
  --set "s5.bcell.esm_dir=\"$T/cache/bepipred_esm\"" --set s7.boltz.msa=empty \
  --set "finetune.evidence=\"$E/s9_5_adaption_evidence.real_9b.json\"" --set "finetune.manifest=\"$E/s9_5_manifest.real_4b.trimmed.json\""
.venv/bin/python -m vaxforge report runs/real_fixture --out runs/real_fixture/report.html
