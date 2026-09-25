import json
p=json.load(open('/tmp/cm/parts.json'))
s=open('/tmp/cm/template.html').read()
grads=p.pop('grads')
s=s.replace('__GRADS__',grads).replace('__PARTS__',json.dumps(p,ensure_ascii=False))
import os
out=os.environ.get('OUT','/tmp/cm/index.html')
os.makedirs(os.path.dirname(out),exist_ok=True)
open(out,'w').write(s)
print(out,len(s))
