import json
import os

src = os.path.dirname(os.path.abspath(__file__))
p = json.load(open(os.path.join(src, 'parts.json')))
s = open(os.path.join(src, 'template.html')).read()
grads = p.pop('grads')
s = s.replace('__GRADS__', grads).replace('__PARTS__', json.dumps(p, ensure_ascii=False))
out = os.environ.get('OUT', os.path.join(src, '..', 'index.html'))
os.makedirs(os.path.dirname(out), exist_ok=True)
open(out, 'w').write(s)
print(out, len(s))
