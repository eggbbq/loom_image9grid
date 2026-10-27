// Capture the actual CLI-built project, including its exported resource metadata.
import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import {readFile, stat, writeFile} from 'node:fs/promises';
import {resolve, extname, sep} from 'node:path';
import {fileURLToPath} from 'node:url';

const require = createRequire(import.meta.url);
const repo = fileURLToPath(new URL('../', import.meta.url));
const {chromium} = require(process.env.NINEGRID_PLAYWRIGHT_MODULE || require.resolve('playwright', {paths: [process.cwd(), repo]}));
const [projectArg, phase] = process.argv.slice(2);
if (!projectArg || !['before', 'after'].includes(phase)) throw new Error('Usage: native_render.mjs project before|after');
const project = resolve(projectArg);
const release = resolve(project, 'release/web');
const artifact = resolve(repo, 'out/validation/ninegrid/native-' + phase);
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
    const page = await browser.newPage({viewport: {width: 640, height: 360}, deviceScaleFactor: 1});
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));
    page.on('response', r => { if (r.status() >= 400 && !r.url().endsWith('favicon.ico')) errors.push(r.status() + ': ' + r.url()); });
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.waitForFunction(() => {
        const find = n => n?.name === 'Panel' ? n : Array.from({length: n?.numChildren || 0}, (_, i) => find(n.getChildAt(i))).find(Boolean);
        return !!find(window.Laya?.stage)?.texture;
    }, {}, {timeout: 60000});
    const runtime = await page.evaluate(() => {
        const find = n => n?.name === 'Panel' ? n : Array.from({length: n?.numChildren || 0}, (_, i) => find(n.getChildAt(i))).find(Boolean);
        const node = find(Laya.stage);
        if (!(node instanceof Laya.GImage)) throw new Error('Expected native New UI GImage');
        return {engine: Laya.LayaEnv.version, type: 'GImage', src: node.src,
            x: node.x, y: node.y, width: node.width, height: node.height,
            textureSize: [node.texture.sourceWidth, node.texture.sourceHeight],
            sizeGrid: node.texture._sizeGrid || null, filterMode: node.texture.bitmap.filterMode};
    });
    if (runtime.engine !== '3.4.1' || runtime.width !== 600 || runtime.height !== 300 || runtime.x !== 12 || runtime.y !== 14)
        throw new Error('Native UI geometry changed: ' + JSON.stringify(runtime));
    if (JSON.stringify(runtime.textureSize) !== JSON.stringify(phase === 'before' ? [600, 300] : [33, 33]))
        throw new Error('Wrong exported texture: ' + JSON.stringify(runtime));
    if (phase === 'after' && JSON.stringify(runtime.sizeGrid) !== '[16,16,16,16,0]')
        throw new Error('Native resource importer did not supply sizeGrid');
    if (errors.length) throw new Error(errors.join('\n'));
    await page.screenshot({path: artifact + '.png'});
    await writeFile(artifact + '.json', JSON.stringify(runtime, null, 2) + '\n');
    console.log(JSON.stringify(runtime));
} finally {
    await browser.close();
    await new Promise(done => server.close(done));
}
