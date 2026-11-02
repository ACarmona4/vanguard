import test from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

test('dashboard and recommendation cards render numeric evidence after savings removal', async () => {
  const result = await build({
    stdin: {contents: `
      import React from 'react';
      import {renderToStaticMarkup} from 'react-dom/server';
      import {FindingCard} from './src/recommendations/RecommendationComponents.jsx';
      const finding = {title:'Messages waiting',provider:'gcp',resource_type:'pubsub_subscription',resource_name:'queue',resource_id:1,severity:'critical',priority:'critical',kind:'alert',category:'reliability',actions:[],evidence:{value:900,unit:'s',metric:'oldest_message_seconds'}};
      export default [true,false].map(compact => renderToStaticMarkup(React.createElement(FindingCard,{finding,compact})));
    `, resolveDir:fileURLToPath(new URL('..',import.meta.url)), loader:'jsx'},
    bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',loader:{'.css':'empty'},
  });
  const module = {exports:{}};
  new Function('require','module','exports',result.outputFiles[0].text)(createRequire(import.meta.url),module,module.exports);
  const cards = module.exports.default;
  for (const html of cards) {assert.match(html,/Oldest message/);assert.match(html,/15 min/);}
});
