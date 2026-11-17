// Rebuild unmodified pinned Pancake V3 Solidity using its upstream settings.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const solc = require('./toolchain/node_modules/solc');
const root = __dirname;
const upstream = path.join(root, 'sources/pancake-v3/projects');
const out = path.join(root, 'artifacts');
fs.mkdirSync(out, {recursive:true});
function sha(b) { return crypto.createHash('sha256').update(b).digest('hex'); }
function resolve(key, project) {
  if (key.startsWith('@pancakeswap/v3-core/')) return path.join(upstream, 'v3-core', key.slice('@pancakeswap/v3-core/'.length));
  if (key.startsWith('@pancakeswap/v3-lm-pool/')) return path.join(upstream, 'v3-lm-pool', key.slice('@pancakeswap/v3-lm-pool/'.length));
  if (key.startsWith('@openzeppelin/')) return path.join(root, 'sources/vendor', key);
  return path.join(upstream, project, key);
}
function closure(entry, project) {
  const sources = {};
  const origins = {};
  function visit(key) {
    if (sources[key]) return;
    const sourcePath = resolve(key, project);
    const content = fs.readFileSync(sourcePath, 'utf8');
    sources[key] = {content};
    origins[key] = {path:path.relative(root, sourcePath), sha256:sha(Buffer.from(content))};
    for (const match of content.matchAll(/import\s+(?:[^;'"\n]*?from\s+)?['"]([^'"]+)['"]\s*;/g)) {
      const dep = match[1].startsWith('.') ? path.posix.normalize(path.posix.join(path.posix.dirname(key), match[1])) : match[1];
      visit(dep);
    }
  }
  visit(entry);
  return {sources:Object.fromEntries(Object.entries(sources).sort()), origins};
}
const jobs = [
  ['PancakeV3PoolDeployer','v3-core','contracts/PancakeV3PoolDeployer.sol',400,['PancakeV3PoolDeployer','PancakeV3Pool']],
  ['PancakeV3Factory','v3-core','contracts/PancakeV3Factory.sol',1000000,['PancakeV3Factory']],
  ['SwapRouter','v3-periphery','contracts/SwapRouter.sol',1000000,['SwapRouter']],
  ['NonfungiblePositionManager','v3-periphery','contracts/NonfungiblePositionManager.sol',2000,['NonfungiblePositionManager']],
];
let manifest = {upstream:'https://github.com/pancakeswap/pancake-v3-contracts', revision:'986847948755cba528324d41be19480731c36c2a', compiler:solc.version(), compiler_soljson_sha256:sha(fs.readFileSync(path.join(root,'toolchain/node_modules/solc/soljson.js'))), jobs:[]};
for (const [name,project,entry,runs,names] of jobs) {
  const {sources,origins} = closure(entry,project);
  const input = {language:'Solidity', sources, settings:{optimizer:{enabled:true,runs}, evmVersion:'istanbul', metadata:{bytecodeHash:'none'}, outputSelection:{'*':{'*':['abi','evm.bytecode','evm.deployedBytecode','storageLayout','metadata']}}}};
  const serialized = JSON.stringify(input);
  fs.writeFileSync(path.join(out,name+'.input.json'),serialized+'\n');
  const output = JSON.parse(solc.compile(serialized));
  const errors = (output.errors || []).filter(e => e.severity==='error');
  if (errors.length) throw new Error(errors.map(e => e.formattedMessage).join('\n'));
  fs.writeFileSync(path.join(out,name+'.output.json'),JSON.stringify(output)+'\n');
  for (const contractName of names) {
    const found = Object.entries(output.contracts).find(([key,value])=>value[contractName]);
    if (!found) throw new Error('Missing output '+contractName);
    const [key,value] = found;
    const artifact = value[contractName];
    fs.writeFileSync(path.join(out,contractName+'.json'),JSON.stringify({contractName, sourceName:key, ...artifact},null,2)+'\n');
    fs.writeFileSync(path.join(out,contractName+'.bin'),artifact.evm.bytecode.object+'\n');
    fs.writeFileSync(path.join(out,contractName+'.runtime-template.bin'),artifact.evm.deployedBytecode.object+'\n');
    console.log(contractName, 'creation', artifact.evm.bytecode.object.length/2, 'runtime', artifact.evm.deployedBytecode.object.length/2);
  }
  manifest.jobs.push({name, entry, optimizer_runs:runs, evm_version:'istanbul', metadata_bytecode_hash:'none', sources:origins, standard_json_sha256:sha(Buffer.from(serialized+'\n'))});
}
const wethFixturePath = path.join(upstream,'v3-periphery/test/contracts/WETH9.json');
const weth = JSON.parse(fs.readFileSync(wethFixturePath));
fs.writeFileSync(path.join(out,'WETH9.bin'),weth.bytecode.replace(/^0x/,'')+'\n');
manifest.wrapped_native_dependency = {type:'unmodified upstream creation-bytecode fixture (not recompiled)',source_path:path.relative(root,wethFixturePath),source_file_sha256:sha(fs.readFileSync(wethFixturePath)),claim:'Canonical WETH9 test fixture supplied by pinned Pancake V3 source; native deposits are real local transactions, no token storage writes.'};
fs.writeFileSync(path.join(out,'solidity-provenance.json'),JSON.stringify(manifest,null,2)+'\n');
