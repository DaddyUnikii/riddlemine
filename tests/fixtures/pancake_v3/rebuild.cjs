// Replay complete pinned Solidity standard-JSON inputs, no source fetches.
// Dependency build only; do not modify pinned artifacts on a rebuild mismatch.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const root = __dirname;
const solc = require('./toolchain/node_modules/solc');
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'artifacts/manifest.json')));
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const compilerBytes = fs.readFileSync(path.join(root, 'toolchain/node_modules/solc/soljson.js'));
if (sha(compilerBytes) !== manifest.solc_soljson_sha256 || solc.version() !== manifest.solc_version) {
  throw new Error('Pinned Solidity compiler mismatch');
}
const jobs = {
  PancakeV3PoolDeployer: ['PancakeV3PoolDeployer', 'PancakeV3Pool'],
  PancakeV3Factory: ['PancakeV3Factory'],
  SwapRouter: ['SwapRouter'],
  NonfungiblePositionManager: ['NonfungiblePositionManager']
};
for (const [name, outputs] of Object.entries(jobs)) {
  const inputPath = `artifacts/${name}.input.json`;
  const bytes = fs.readFileSync(path.join(root, inputPath));
  if (sha(bytes) !== manifest.files[inputPath].sha256) {
    throw new Error('Compiler source input mismatch: ' + name);
  }
  const output = JSON.parse(solc.compile(bytes.toString()));
  const errors = (output.errors || []).filter(item => item.severity === 'error');
  if (errors.length) throw new Error(errors.map(item => item.formattedMessage).join('\n'));
  for (const contractName of outputs) {
    const found = Object.values(output.contracts).find(item => item[contractName]);
    if (!found) throw new Error('Missing compiler output: ' + contractName);
    const rebuilt = Buffer.from(found[contractName].evm.bytecode.object, 'hex');
    const pinned = Buffer.from(fs.readFileSync(path.join(root, `artifacts/${contractName}.bin`), 'utf8').trim(), 'hex');
    if (!rebuilt.equals(pinned)) throw new Error('Rebuilt creation bytes mismatch: ' + contractName);
    console.log(contractName + ' exact creation bytes verified: ' + rebuilt.length);
  }
}
console.log('All five official source-built creation artifacts match exactly. WETH9 upstream JSON creation fixture is not recompiled.');
