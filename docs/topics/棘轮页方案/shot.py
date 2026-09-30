import asyncio, os, pathlib
from playwright.async_api import async_playwright
HERE=pathlib.Path(__file__).parent
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch()
        errs=[]
        for w in (1440,1100):
            pg=await b.new_page(viewport={'width':w,'height':900}, device_scale_factor=1.5)
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.on('console', lambda m: m.type=='error' and errs.append(m.text))
            await pg.goto((HERE/'index.html').as_uri())
            await pg.screenshot(path=str(HERE/f'shots/full-{w}.png'), full_page=True)
            if w==1440:
                await pg.screenshot(path=str(HERE/'shots/first-screen.png'))
                # expand the C2 row and the fe boundary row, then hover a sparkline
                await pg.click('tr[data-d="d-c2"]'); await pg.click('tr[data-d="d-feb"]')
                assert await pg.is_visible('#d-c2') and await pg.is_visible('#d-feb')
                s=pg.locator('#s2'); await s.scroll_into_view_if_needed()
                box=await pg.locator('svg[data-key="fe_boundary"]').bounding_box()
                await pg.mouse.move(box['x']+box['width']*0.55, box['y']+box['height']/2)
                tip=await pg.inner_text('#tip'); print('tooltip:', tip.replace('\n',' | '))
                await pg.screenshot(path=str(HERE/'shots/s2-expanded.png'), clip={'x':0,'y':(await s.bounding_box())['y']-10,'width':1440,'height':900})
                await pg.click('tr[data-d="d-scene"]')
                s1=pg.locator('#s1'); await s1.scroll_into_view_if_needed()
                bb=await s1.bounding_box(); await pg.screenshot(path=str(HERE/'shots/s1-expanded.png'), clip={'x':0,'y':bb['y']-10,'width':1440,'height':bb['height']+20})
            # overflow check
            ov=await pg.evaluate("[...document.querySelectorAll('.page *')].filter(e=>e.scrollWidth>e.clientWidth+1 && getComputedStyle(e).overflowX!=='visible' ).length")
            print(w,'overflowing elements:',ov, 'doc width', await pg.evaluate('document.documentElement.scrollWidth'))
        print('errors:', errs)
        await b.close()
asyncio.run(main())
