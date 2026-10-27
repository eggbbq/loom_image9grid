import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import {readFile, stat, writeFile, mkdir} from 'node:fs/promises';
import {resolve, extname, sep} from 'node:path';
import {fileURLToPath} from 'node:url';
const require = createRequire(import.meta.url);
const repo = fileURLToPath(new URL('../', import.meta.url));
const {chromium} = require(process.env.NINEGRID_PLAYWRIGHT_MODULE || require.resolve('playwright', {paths: [process.cwd(), repo]}));
const [projectArg, phase, artifactsArg] = process.argv.slice(2);
if (!projectArg || !['before', 'after'].includes(phase)) throw new Error('Expected project before|after');
const project = resolve(projectArg), release = resolve(project, 'release/web');
const artifacts = resolve(artifactsArg || resolve(repo, 'out/validation/ninegrid/visual-examples'));
await mkdir(artifacts, {recursive: true});
const server = createServer(async (req, res) => {
    try {
        let path = resolve(release, '.' + decodeURIComponent(new URL(req.url, 'http://localhost').pathname));
        if (!path.startsWith(release + sep) && path !== release) throw new Error('Unsafe path');
        if ((await stat(path)).isDirectory()) path = resolve(path, 'index.html');
        res.writeHead(200, {'Content-Type': {'.html': 'text/html', '.js': 'application/javascript',
            '.json': 'application/json', '.ls': 'application/json', '.lh': 'application/json', '.png': 'image/png'}[extname(path)] || 'application/octet-stream'});
        res.end(await readFile(path));
    } catch { res.writeHead(404); res.end('Not found'); }
});
await new Promise(done => server.listen(0, '127.0.0.1', done));
const browser = await chromium.launch({channel: process.env.NINEGRID_BROWSER || 'chrome'});
try {
    const page = await browser.newPage({viewport: {width: 800, height: 1340}, deviceScaleFactor: 1});
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));
    page.on('response', r => { if (r.status() >= 400 && !r.url().endsWith('favicon.ico')) errors.push(r.status() + ': ' + r.url()); });
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.waitForFunction(() => {
        const find = (n, name) => n?.name === name ? n : Array.from({length: n?.numChildren || 0}, (_, i) => find(n.getChildAt(i), name)).find(Boolean);
        return ['Panel68', 'Poster37'].every(name => !!find(window.Laya?.stage, name)?.texture);
    }, {}, {timeout: 60000});
    const data = await page.evaluate(() => {
        const find = (n, name) => n?.name === name ? n : Array.from({length: n?.numChildren || 0}, (_, i) => find(n.getChildAt(i), name)).find(Boolean);
        return ['Panel68', 'Poster37'].map(name => {
            const node = find(Laya.stage, name);
            if (!(node instanceof Laya.GImage)) throw new Error('Expected GImage');
            return {name, src: node.src, x: node.x, y: node.y, width: node.width, height: node.height,
                sourceSize: [node.texture.sourceWidth, node.texture.sourceHeight],
                bitmapSize: [node.texture.bitmap.width, node.texture.bitmap.height],
                grid: node.texture._sizeGrid || null, filterMode: node.texture.bitmap.filterMode};
        });
    });
    for (const item of data) {
        const expected = item.name === 'Panel68' ? [575, 172, 20, 20] : [738, 1078, 20, 220];
        if (JSON.stringify([item.width, item.height, item.x, item.y]) !== JSON.stringify(expected)) throw new Error('Geometry changed');
        if (phase === 'before' && JSON.stringify(item.sourceSize) !== JSON.stringify(expected.slice(0, 2))) throw new Error('Wrong original texture');
        if (phase === 'after') {
            const size = item.name === 'Panel68' ? [70, 82] : [738, 592];
            if (JSON.stringify(item.sourceSize) !== JSON.stringify(size) || !item.grid) throw new Error('Native ninegrid not imported');
        }
        await page.screenshot({path: resolve(artifacts, `${item.name}-${phase}.png`),
            clip: {x: item.x, y: item.y, width: item.width, height: item.height}});
    }
    if (errors.length) throw new Error(errors.join('\n'));
    await page.screenshot({path: resolve(artifacts, `native-${phase}.png`)});
    await writeFile(resolve(artifacts, `native-${phase}.json`), JSON.stringify(data, null, 2) + '\n');
    console.log(JSON.stringify(data));
} finally {
    await browser.close();
    await new Promise(done => server.close(done));
}
