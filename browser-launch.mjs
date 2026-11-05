// Fixed per-owner workspace; MCP clients cannot expand the file roots via roots/list.
import fs from 'node:fs';
import {pathToFileURL} from 'node:url';
const configPath = process.argv[2];
const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
process.chdir(config.workspace);
const cli = 'D:/CodexTelegram/runtime/browser/node_modules/@playwright/mcp/cli.js';
process.argv = [process.execPath, cli, '--config', configPath, '--ignore-roots'];
await import(pathToFileURL(cli).href);
