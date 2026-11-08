import sys,re,os
dist,out,label=sys.argv[1],sys.argv[2],sys.argv[3]
html=open(os.path.join(dist,'index.html')).read()
html=re.sub(r'<link rel="modulepreload"[^>]*>\s*','',html)
def css(m):
    c=open(os.path.join(dist,m.group(1))).read()
    # 只留 woff2 一份图标字体：别的格式同一套字形，只会把单文件撑过截图服务的 5 MB 上限
    c=re.sub(r'url\(data:application/vnd\.ms-fontobject[^)]*\)(\s*format\([^)]*\))?\s*,?','',c)
    c=re.sub(r'url\(data:font/(ttf|woff);[^)]*\)\s*format\([^)]*\)\s*,?','',c)
    c=re.sub(r'src:\s*;','',c)
    return '<style>'+c+'</style>'
html=re.sub(r'<link rel="stylesheet"[^>]*href="\./([^"]+)"[^>]*>',css,html)
def js(m):
    code=open(os.path.join(dist,m.group(1))).read().replace('</script','<\\/script')
    return '<script type="module">'+code+'</script>'
html=re.sub(r'<script type="module"[^>]*src="\./([^"]+)"></script>',js,html)
os.makedirs(out,exist_ok=True)
for s in ['pending','empty','failed','reread','reread-fail']:
    h=html.replace("new URLSearchParams(location.search).get('s') || 'pending'", repr(s))
    h=h.replace('__BUILD__', sys.argv[4]).replace('<script>window.__BUILD', '<script>window.__SHOTS=1;window.__BUILD')
    open(os.path.join(out,f'{label}-{s}.html'),'w').write(h)
open(os.path.join(out,f'{label}.html'),'w').write(html.replace('__BUILD__', sys.argv[4]))
print(len(html))
