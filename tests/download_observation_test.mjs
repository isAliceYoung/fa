import assert from 'node:assert/strict';
import {matchHistory,rejectedUI} from './download_observation.mjs';
const task={url:'https://203.0.113.2/file/reset?task='+'a'.repeat(32),beforeIds:[1]};
const row={id:2,guid:'known-guid',urls:[task.url],target_path:'/home/wdguest/Downloads/d1-reset.bin',state:3};
const cases=[
 ['bound task and exact target',()=>assert.equal(matchHistory([row],task,'d1-reset.bin'),row)],
 ['another new failed task cannot pass',()=>assert.equal(matchHistory([{...row,urls:[task.url.replace('reset','cancel')]}],task),null)],
 ['older same-name task cannot pass',()=>assert.equal(matchHistory([{...row,urls:[task.url.replace(/a{32}/,'b'.repeat(32))]}],task),null)],
 ['pre-existing ID cannot pass',()=>assert.equal(matchHistory([{...row,id:1}],task),null)],
 ['wrong target cannot pass',()=>assert.equal(matchHistory([row],task,'other.bin'),null)],
 ['outside data target cannot pass',()=>assert.equal(matchHistory([{...row,target_path:'/root/d1-reset.bin'}],task),null)],
 ['missing URL or GUID is unavailable',()=>{assert.equal(matchHistory([{...row,urls:[]}],task),null);assert.equal(matchHistory([{...row,guid:null}],task),null);}],
 ['ambiguous rows are unavailable',()=>assert.equal(matchHistory([row,{...row,id:3}],task),null)],
 ['late unrelated history remains unavailable',()=>assert.equal(matchHistory([{...row,id:4,urls:['https://203.0.113.2/file/spaces?task='+'c'.repeat(32)]}],task),null)],
 ['File incomplete only for failed bound reset UI',()=>{const ui={name_visible:true,other_names_visible:[],failed:true,file_incomplete:true};assert(rejectedUI('reset',ui));assert(!rejectedUI('space',ui));assert(!rejectedUI('cancel',ui));assert(!rejectedUI('reset',{...ui,other_names_visible:['other']}));assert(!rejectedUI('reset',{...ui,failed:false}));}],
];
let passed=0;for(const [name,fn]of cases){fn();passed++;console.log('PASS '+name);}
console.log(JSON.stringify({passed,failed:0,skipped:0,not_executed:0}));
