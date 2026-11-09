"""Deterministic Chinese UI text; no auxiliary model or prompt rewriting."""
from pathlib import Path


def failure_text(error):
    error = error if isinstance(error, dict) else {}
    code = error.get('codexErrorInfo', error.get('codex_error_info'))
    if code in ('serverOverloaded', 'server_overloaded'):
        reason = '模型服務目前滿載，這次工作中途停止。'
    else:
        reason = '這次工作因服務錯誤中途停止，目前沒有可確認的完成結果。'
    return reason + '\n不代表外部操作已取消：外部服務可能仍在處理已送出的任務。目前已停止，不會繼續自動重試。稍後可傳「先查上一個任務的結果，避免重複建立；再接續未完成的部分」。'

COMMANDS = [
    ('usage','查看 token 使用量與 API 費用估算'),
    ('trust','管理本人 MCP 信任與撤銷'),
    ('secrets','查看本人憑證名稱；請勿在聊天貼 key'),
    ('memory','查看、新增、更正或忘記個人記憶'),
    ('cron','查看排程；加上文字可建立或管理'),
    ('help','查看操作說明'), ('new','開始新的對話'), ('sessions','查看過去對話'),
    ('use','切換到指定對話'), ('skills','選擇要使用的技能'), ('status','查看目前進度'),
    ('stop','停止目前工作'), ('steer','補充目前工作要求'), ('compact','整理目前對話'),
    ('model','查看或選擇模型'), ('effort','查看或選擇思考程度'),
    ('plan','先討論計劃'), ('default','切回一般工作模式'),
    ('result','重新取回最近回答'), ('get','下載工作區檔案'),
    ('login','登入本人已設定的外部服務'),
    ('login_status','確認自己的外部服務連線'),
    ('login_cancel','取消進行中的登入'),
]


def approval_text(method, params, workspace):
    if method == 'item/fileChange/requestApproval':
        action = 'Codex 想修改檔案，需要你確認。'
    elif method == 'item/permissions/requestApproval':
        action = 'Codex 需要目前工作範圍以外的權限。'
    else:
        action = 'Codex 想執行需要額外確認的操作。'
    lines = [action]
    if params.get('reason'):
        lines.append('用途：' + params['reason'])
    actions = params.get('commandActions') or []
    labels = {'read':'讀取檔案','listFiles':'查看資料夾','search':'搜尋內容','unknown':'執行本機指令'}
    for item in actions[:6]:
        label = labels.get(item.get('type'),'執行本機操作')
        target = item.get('path') or item.get('name') or ''
        lines.append('操作：' + label + (' — ' + str(target) if target else ''))
    cwd = params.get('cwd')
    if cwd:
        location = '獨立工作區' if Path(cwd).resolve() == Path(workspace).resolve() else cwd
        lines.append('位置：' + location)
    if params.get('networkApprovalContext'):
        context = params['networkApprovalContext']
        lines.append('連線對象：' + str(context.get('host') or context.get('hostname') or '請查看細節'))
    if method == 'item/permissions/requestApproval':
        permissions = params.get('permissions') or {}
        if permissions.get('network'):
            lines.append('需要：網路存取')
        if permissions.get('fileSystem'):
            lines.append('需要：額外檔案存取（可查看細節）')
    lines.append('可先點「查看細節」。此確認 10 分鐘內有效。')
    return '\n'.join(lines)


def approval_details(params):
    lines = []
    if params.get('command'):
        lines.extend(['實際執行內容：', params['command']])
    if params.get('cwd'):
        lines.append('工作位置：' + params['cwd'])
    if params.get('reason'):
        lines.append('用途：' + params['reason'])
    def flatten(value, label):
        if isinstance(value, dict):
            for key, child in value.items():
                flatten(child, label + ' / ' + key)
        elif isinstance(value, list):
            for child in value:
                flatten(child, label)
        elif value is not None:
            lines.append(label + '：' + str(value))
    for field, label in [('permissions','要求權限'), ('additionalPermissions','額外權限'),
                         ('networkApprovalContext','連線資訊'), ('grantRoot','檔案範圍')]:
        if params.get(field):
            flatten(params[field],label)
    return '\n'.join(lines) or 'Codex 沒有提供額外細節。'
