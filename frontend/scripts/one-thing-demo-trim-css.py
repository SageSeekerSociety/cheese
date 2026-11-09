# Shrink the single-file demo's CSS: drop KaTeX faces (no math in the demo) and keep only the woff2 source of every other face.
import re,sys,glob
css=glob.glob(sys.argv[1]+'/assets/style-*.css')[0]
s=open(css,encoding='utf-8').read()
def fix(m):
    b=m.group(0)
    if 'KaTeX_' in b: return ''
    w=re.search(r'url\((data:font/woff2;base64,[A-Za-z0-9+/=]+)\)',b)
    if not w: return b
    fam=re.search(r'font-family:([^;}]+)',b).group(1)
    rest=''.join(f'{k}:{v};' for k,v in re.findall(r'(font-weight|font-style|font-display|unicode-range):([^;}]+)',b))
    return '@font-face{font-family:%s;src:url(%s) format("woff2");%s}'%(fam,w.group(1),rest)
s2=re.sub(r'@font-face\{(?:[^}]|\}(?!))*?\}',fix,s)
open(css,'w',encoding='utf-8').write(s2)
print(round(len(s)/1e6,2),'->',round(len(s2)/1e6,2),'MB')
