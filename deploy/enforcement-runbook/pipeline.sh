#!/usr/bin/env bash
# Per sidecared workload in pharmacy: pod, image tag, outbound chain, lineage values (in/out), the gate's config as
# /v1/pipeline echoes it (secret shown only as masked or not). usage: check-pipeline.sh [workload...] [--full]
source "$(dirname "$0")/env.sh" || exit 9
full=0; ws=(); for a in "$@"; do [ "$a" = --full ] && full=1 || ws+=("$a"); done
[ ${#ws[@]} = 0 ] && ws=($(kubectl -n $NS get deploy -l rossoctl.io/type -o jsonpath='{.items[*].metadata.name}'))
for w in "${ws[@]}"; do
  pod=$(kubectl -n $NS get pod -l app.kubernetes.io/name=$w --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')
  img=$(kubectl -n $NS get pod $pod -o jsonpath='{.spec.containers[?(@.name=="authbridge-proxy")].image}')
  kubectl -n $NS exec $pod -c authbridge-proxy -- wget -qO- http://127.0.0.1:9094/v1/pipeline 2>/dev/null | FULL=$full python3 -c "
import json,os,sys
d=json.load(sys.stdin); out=[p['name'] for p in d['outbound']]
lin=lambda dirn:[p for p in d[dirn] if p['name']=='lineage-telemetry']
li,lo=lin('inbound'),lin('outbound')
g=[p for p in d['outbound'] if p['name']=='sentry-gate']
gc=dict(g[0].get('config',{})) if g else {}
sec=gc.pop('verdict_secret',None)
summary={k:gc[k] for k in sorted(gc)}
if 'fail_mode_revision' in summary and not os.environ.get('FULL')=='1': summary['fail_mode_revision']=summary['fail_mode_revision'][:12]
print('$w', '$pod'.rsplit('-',2)[-1], '${img##*:}', 'out=' + '>'.join(out),
  'capture_io=%s/%s' % (li[0]['config'].get('capture_io') if li else '-', lo[0]['config'].get('capture_io') if lo else '-'),
  'delay=%s/%s' % (li[0]['config'].get('export_delay_ms') if li else '-', lo[0]['config'].get('export_delay_ms') if lo else '-'),
  'mode=%s' % ((g[0].get('onError') or 'enforce') if g else '-'), 'secret=%s' % sec, 'gate=' + json.dumps(summary, separators=(',',':')))"
done
