// Verification only: this file never edits application TypeScript or scenes.
import {createRequire} from 'node:module';
import {readFile, writeFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {dirname, resolve, relative} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';

const require = createRequire(import.meta.url);
const here = dirname(fileURLToPath(import.meta.url));
let input = '';
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const modulePath = process.env.NINEGRID_PLAYWRIGHT_MODULE || require.resolve('playwright', {
    paths: [config.project, process.cwd(), here]
});
const {chromium} = require(modulePath);
const libraries = ['laya.core.js', 'laya.webgl_2D.js', 'laya.ui.js', 'laya.ui2.js'];
const server = createServer(async (req, res) => {
    const name = req.url.slice(1);
    if (!libraries.includes(name)) {
        res.writeHead(200, {'Content-Type': 'text/html'});
        res.end(libraries.map(lib => `<script src="/${lib}"></script>`).join(''));
    } else {
        res.writeHead(200, {'Content-Type': 'application/javascript'});
        res.end(await readFile(resolve(config.libraries, name)));
    }
});
await new Promise(ready => server.listen(0, '127.0.0.1', ready));
let browser;
try {
    browser = await chromium.launch({channel: process.env.NINEGRID_BROWSER || 'chrome'});
    const page = await browser.newPage({deviceScaleFactor: 1});
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    const dimensions = config.cases.map(c => c.sourceSize);
    const width = Math.ceil(Math.max(...dimensions.map(d => d[0])) * 1.2) + 64;
    const height = Math.ceil(Math.max(...dimensions.map(d => d[1])) * 1.2) + 64;
    if (width > 8192 || height > 8192) throw new Error('Probe canvas exceeds 8192 pixels');
    await page.setViewportSize({width, height});
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.evaluate(async ({width, height, version}) => {
        if (Laya.LayaEnv.version !== version) throw new Error('Unverified engine version');
        Laya.PlayerConfig.UI = {alwaysIncludeDefaultSkin: false};
        await Laya.init(width, height);
        Laya.stage.bgColor = '#30363D';
        Laya.stage.scaleMode = 'noscale';
    }, {width, height, version:config.version});
    const results = [];
    for (const c of config.cases) {
        const data = {...c, source: 'data:image/png;base64,' + (await readFile(c.source)).toString('base64'),
            output: 'data:image/png;base64,' + (await readFile(c.output)).toString('base64')};
        const key = createHash('sha256').update((c.candidateKey || c.source) + ':' + c.type + ':' + c.filterMode + ':' + c.pose).digest('hex').slice(0, 16);
        const captures = {};
        for (const phase of ['before', 'after']) {
            const runtime = await page.evaluate(async ({data, phase}) => {
                if (window.probeNode) window.probeNode.destroy(true);
                const src = phase === 'before' ? data.source : data.output;
                const tex = await Laya.loader.load({url:src,type:Laya.Loader.IMAGE,propertyParams:{anisoLevel:data.anisoLevel}});
                if (!tex) throw new Error('Texture failed to load');
                tex.bitmap.filterMode = data.filterMode; tex.bitmap.anisoLevel=data.anisoLevel;
                tex._sizeGrid = phase === 'after' ? data.sizeGrid.split(',').map(Number) : null;
                const fields = {_$ver: 1, _$id: 'ninegrid-probe', _$type: data.type,
                    name: 'Probe', x: 16 + data.offsetX, y: 16 + data.offsetY, scaleX: data.renderScale, scaleY: data.renderScale, width: data.sourceSize[0], height: data.sourceSize[1]};
                if (data.type === 'Image') {
                    fields.skin = src;
                    if (phase === 'after') fields.sizeGrid = data.sizeGrid;
                } else { fields.src = src; fields.autoSize = false; }
                const errors = [];
                const node = new Laya.PrefabImpl(null, fields).create(null, errors);
                if (errors.length) throw new Error(JSON.stringify(errors));
                if (node.constructor !== Laya[data.type]) throw new Error('Wrong native node class');
                Laya.stage.addChild(node);
                window.probeNode = node;
                await new Promise(done => requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(done))));
                if ((node.source || node.texture) !== tex) throw new Error('Native node did not load the optimized resource');
                if (node.width !== data.sourceSize[0] || node.height !== data.sourceSize[1]) throw new Error('UI dimensions changed');
                return {width: node.width, height: node.height, textureSize: [tex.sourceWidth, tex.sourceHeight],
                    sizeGrid: node.sizeGrid || tex._sizeGrid, anisoLevel:tex.bitmap.anisoLevel};
            }, {data, phase});
            const path = resolve(config.artifacts, `${key}-${phase}.png`);
            await page.screenshot({path, clip: {x: 16, y: 16, width: Math.ceil(c.sourceSize[0] * c.renderScale) + 2, height: Math.ceil(c.sourceSize[1] * c.renderScale) + 2}});
            captures[phase] = relative(config.project, path).split('\\').join('/');
            captures[phase + 'Runtime'] = runtime;
        }
        results.push({candidateKey: c.candidateKey, source: relative(config.project, c.source).split('\\').join('/'),
            type: c.type, filterMode: c.filterMode, pose: c.pose, mode: c.mode || 'strict',
            qualityLimits: c.qualityLimits || null,
            comparisonRegion: {width: Math.ceil(c.sourceSize[0] * c.renderScale) + 2, height: Math.ceil(c.sourceSize[1] * c.renderScale) + 2}, ...captures});
    }
    if (errors.length) throw new Error(errors.join('\n'));
    process.stdout.write(JSON.stringify(results));
} finally {
    if (browser) await browser.close();
    await new Promise(done => server.close(done));
}
