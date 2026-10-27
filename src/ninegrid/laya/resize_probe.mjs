// Generic real-engine resize validation for automatically optimized textures.
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
const require=createRequire(import.meta.url);

let input='';for await(const chunk of process.stdin)input+=chunk;
const config=JSON.parse(input);
const {chromium}=require(process.env.NINEGRID_PLAYWRIGHT_MODULE||require.resolve('playwright',{paths:[config.project||process.cwd(),process.cwd()]}));
const libs=['laya.core.js','laya.webgl_2D.js','laya.ui.js','laya.ui2.js'];
const server=createServer(async(req,res)=>{
 const name=req.url.slice(1);res.writeHead(200,{'Content-Type':libs.includes(name)?'application/javascript':'text/html'});
 res.end(libs.includes(name)?await readFile(join(config.libraries,name)):libs.map(n=>`<script src="/${n}"></script>`).join(''));
});
await new Promise(done=>server.listen(0,'127.0.0.1',done));let browser;
try{
 browser=await chromium.launch({channel:'chrome'});
 const viewport={width:Math.ceil(Math.max(...config.cases.map(c=>c.capture[0])))+64,
  height:Math.ceil(Math.max(...config.cases.map(c=>c.capture[1])))+64};
 if(viewport.width>8192||viewport.height>8192)throw Error('Resize probe exceeds 8192px');
 const page=await browser.newPage({viewport,deviceScaleFactor:1});
 const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 await page.goto(`http://127.0.0.1:${server.address().port}/`);
 await page.evaluate(async({width,height,version})=>{
  if(Laya.LayaEnv.version!==version)throw Error('Wrong engine');
  Laya.PlayerConfig.UI={alwaysIncludeDefaultSkin:false};await Laya.init(width,height);
  Laya.stage.scaleMode='noscale';Laya.stage.bgColor='#30363D';
 },{...viewport,version:config.version});
 const results=[];
 for(const c of config.cases){
  const key=createHash('sha256').update(JSON.stringify(c)).digest('hex').slice(0,16);
  const data={...c,original:'data:image/png;base64,'+(await readFile(c.original)).toString('base64'),
   optimized:'data:image/png;base64,'+(await readFile(c.optimized)).toString('base64')};
  const captures={};
  for(const phase of ['before','after']){
   const runtime=await page.evaluate(async({data,phase})=>{
    window.probe?.destroy(true);
    const src=phase==='before'?data.original:data.optimized;
    const tex=await Laya.loader.load({url:src,type:Laya.Loader.IMAGE,propertyParams:{anisoLevel:data.anisoLevel??4}});tex.bitmap.filterMode=data.filterMode;
    if(data.anisoLevel!=null)tex.bitmap.anisoLevel=data.anisoLevel;
    // The full original texture with the computed fixed borders is the
    // reference for resizing; compression must preserve these nine-slice semantics.
    tex._sizeGrid=data.grid;
    const image={_$id:'fill',_$type:'GImage',name:'fill',width:data.sourceSize[0],height:data.sourceSize[1],src,autoSize:false};
    const fields=data.owner?{_$id:'owner',_$type:data.owner.type,name:'owner',width:data.owner.width,height:data.owner.height,
     min:0,max:100,value:100,[data.owner.axis==='horizontal'?'_hBar':'_vBar']:{_$ref:'fill'},_$child:[image]}:
     {...image,width:data.target[0],height:data.target[1]};
    const failures=[];const node=new Laya.PrefabImpl(null,fields).create(null,failures);
    if(failures.length)throw Error(JSON.stringify(failures));node.pos(data.position?.[0]??(data.filterMode===0?16.375:16.5),data.position?.[1]??16.25);Laya.stage.addChild(node);window.probe=node;
    if(data.owner){node.reverse=data.reverse;node.value=data.value;}
    await new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(()=>requestAnimationFrame(done))));
    const fill=data.owner?node.getChildByName('fill'):node;
    if(data.owner){const axis=data.owner.axis==='horizontal'?'width':'height';
     const expected=Math.round(data.sourceSize[axis==='width'?0:1]*data.value/100);
     if(fill[axis]!==expected)throw Error(`Wrong fill ${axis}: ${fill[axis]} != ${expected}`);}
    return {width:fill.width,height:fill.height,x:fill.x,y:fill.y,textureSize:[tex.sourceWidth,tex.sourceHeight],anisoLevel:tex.bitmap.anisoLevel,filterMode:tex.bitmap.filterMode,mipmap:tex.bitmap.mipmap};
   },{data,phase});
   const path=join(config.artifacts,`${key}-${phase}.png`);
   await page.screenshot({path,clip:{x:15,y:15,width:Math.ceil(c.capture[0])+4,height:Math.ceil(c.capture[1])+4}});
   captures[phase]=path;captures[phase+'Runtime']=runtime;
  }
  results.push({source:c.name,type:c.owner?.type||'GImage',value:c.value,reverse:c.reverse,target:c.target,position:c.position,
   mode:c.mode,qualityLimits:c.qualityLimits,...captures});
 }
 if(errors.length)throw Error(errors.join('\n'));
 process.stdout.write(JSON.stringify(results));
}finally{if(browser)await browser.close();await new Promise(done=>server.close(done));}
