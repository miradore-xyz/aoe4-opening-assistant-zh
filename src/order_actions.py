"""将建筑与科技动作保存为可编辑元数据，并生成兼容原格式的提示文字。"""
from __future__ import annotations

import copy
import re


def time_caption(value):
    """分钟与秒直接作为时间点名称；没有时间时不编造时间。"""
    text = str(value or '').strip()
    match = re.fullmatch(r'(\d+):([0-5]\d)', text)
    if match:
        return f'{int(match[1])}分{int(match[2]):02d}秒'
    return text if text else '未设置时间'


def manual_notes(step):
    """只剥离本编辑器之前追加的完整动作段落，保留用户原有正文。"""
    notes = list(step.get('notes', []))
    generated = step.get('_zh_action_notes', [])
    if generated and notes[-len(generated):] == generated:
        return notes[:-len(generated)]
    return notes


def action_notes(actions):
    """动作变成普通文字及随包图标；原版仍能读取notes文字列表。"""
    result = []
    for action in actions:
        name = str(action.get('name_zh') or '').strip()
        if not name:
            continue
        icon = str(action.get('icon') or '')
        prefix = f'@{icon}@ ' if icon else ''
        if action.get('kind') == 'building':
            count = max(1, int(action.get('count', 1)))
            verb = str(action.get('action_verb_zh') or '建造')
            text = verb + name + (f' × {count}' if count > 1 else '')
        else:
            text = str(action.get('action_verb_zh') or '研究') + name
            location = str(action.get('location_zh') or '').strip()
            if location:
                text += f'（{location}）'
        result.append(prefix + text)
    return result


def save_actions(step, actions, notes):
    """同时保存动作和提示，不在每次编辑时重复追加相同动作。"""
    generated = action_notes(actions)
    step['notes'] = list(notes) + generated or ['']
    if actions:
        step['_zh_actions'] = copy.deepcopy(actions)
        step['_zh_action_notes'] = generated
    else:
        step.pop('_zh_actions', None)
        step.pop('_zh_action_notes', None)
