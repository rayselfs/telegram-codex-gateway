"""Explicit, owner-scoped preferences. Native Codex history remains separate."""
import secrets
import time

TOOL = {'type': 'function', 'name': 'gateway_memory',
    'description': 'Manage the current user explicit personal memories. list reads; add/update/forget only when the user explicitly asks to remember, correct or forget. Never store passwords, tokens or OTPs. This does not erase native history or automatically generated Codex memories; explain that distinction.',
    'inputSchema': {'type': 'object', 'properties': {
        'action': {'type': 'string', 'enum': ['list', 'add', 'update', 'forget']},
        'id': {'type': 'string'}, 'text': {'type': 'string'}},
        'required': ['action'], 'additionalProperties': False}}


class PersonalMemory:
    def __init__(self, journal):
        self.journal = journal

    def entries(self):
        return self.journal.get('personal_memories', {})

    def apply(self, args):
        if not isinstance(args, dict) or set(args) - {'action', 'id', 'text'}:
            raise ValueError('記憶參數不符。')
        action, key, text = args.get('action'), args.get('id'), args.get('text', '')
        entries = self.entries()
        if action == 'list':
            return entries
        if action not in ('add', 'update', 'forget') or (action != 'add' and (key not in entries or entries[key]['forgotten'])):
            raise ValueError('找不到這筆個人記憶。')
        if action != 'forget' and (not isinstance(text, str) or not text.strip() or len(text) > 1500):
            raise ValueError('記憶內容需為 1–1500 字。')
        if action == 'add':
            if sum(not v['forgotten'] for v in entries.values()) >= 100:
                raise ValueError('最多 100 筆，請先整理既有記憶。')
            key = secrets.token_hex(4)
        entries[key] = {'text': '' if action == 'forget' else text.strip(),
                        'forgotten': action == 'forget', 'updated': time.time()}
        self.journal.set('personal_memories', entries)
        return {'id': key, 'state': 'forgotten' if action == 'forget' else 'saved'}

    def context(self):
        rows = [f'{k}: {v["text"]}' for k, v in self.entries().items() if not v['forgotten']]
        return '\n'.join(rows)

    async def command(self, controller, arg):
        action, _, rest = arg.partition(' ')
        if action == 'auto':
            request = rest.strip() or '只讀取並整理目前記住的資訊，列出來源。'
            home = controller.tg.inbox.parent / 'codex-home'
            return await controller.start([{'type':'text','text':
                '使用者要求管理本人 Codex 自動記憶：'+request+'\n本人 Codex home：'+str(home)+
                '\n只處理本人 memories 與個人 AGENTS 中相關內容；先確認實際來源。刪除或更正僅限上述明確要求。'
                '不要刪除對話歷史、資料庫或其他使用者資料。若未找到、無法修改或未完全移除，明確說明；不能宣稱已完全忘記。'}])
        if not arg:
            rows = [(k, v) for k, v in self.entries().items() if not v['forgotten']]
            await controller.tg.say('你明確保存的個人記憶：\n' + ('\n'.join(k+'：'+v['text'] for k,v in rows) or '尚無。') +
                '\n\n/memory add 內容\n/memory set 編號 新內容\n/memory forget 編號\n/memory auto 查看自動記憶，也可加上明確的更正或忘記要求。\n忘記此處記憶不會刪除歷史對話與 Codex 自動記憶。')
            await controller.tg.buttons('記憶操作', [[('查看 Codex 自動記憶', 'mem:native')]])
            for k,v in rows[:20]:
                await controller.tg.buttons(v['text'][:100], [[('忘記這筆', 'mem:forget:'+k)]])
            return
        if action == 'add':
            result = self.apply({'action': 'add', 'text': rest})
        elif action == 'set':
            key, _, text = rest.partition(' ')
            result = self.apply({'action': 'update', 'id': key, 'text': text})
        elif action == 'forget':
            result = self.apply({'action': 'forget', 'id': rest})
        else:
            return await controller.start([{'type':'text','text': '管理我的個人記憶，優先使用 gateway_memory：' + arg}])
        await controller.tg.say(('已忘記這筆明確保存的記憶；歷史對話與 Codex 自動記憶未刪除。' if result['state']=='forgotten' else '個人記憶已更新：'+result['id']) + '\n新的對話會使用更新內容。')
