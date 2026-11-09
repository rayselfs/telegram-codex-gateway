// Fixed per-owner workspace; MCP clients cannot expand the file roots via roots/list.
import fs from 'node:fs';
import {pathToFileURL} from 'node:url';
import path from 'node:path';
const configPath = process.argv[2];
const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
process.chdir(config.workspace);
if(!process.env.TELEGRAM_CODEX_CONFIG)throw new Error('Deployment configuration required');
const deployment=JSON.parse(fs.readFileSync(process.env.TELEGRAM_CODEX_CONFIG,'utf8'));
const cli = path.join(deployment.data_root,'runtime/browser/node_modules/@playwright/mcp/cli.js');
process.argv = [process.execPath, cli, '--config', configPath, '--ignore-roots'];
await import(pathToFileURL(cli).href);
