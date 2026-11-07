"""Expose only the academic calendar verified against the official timetable.

This is MGSU's calendar, not ISO week numbering and not an assumed MAI rule.
"""
import json
from datetime import date
from pathlib import Path

def academic_calendars():
    try:
        meta=json.loads((Path(__file__).resolve().parent.parent/'data/academic_calendars.json').read_text(encoding='utf-8'))
        result={}
        for slot,calendar in meta.items():
            start=date.fromisoformat(calendar['term_start'])
            end=date.fromisoformat(calendar['term_end'])
            anchor=date.fromisoformat(calendar['week_one_monday'])
            weeks=calendar['weeks']
            if slot not in ('mai','mgsu') or not isinstance(weeks,int) or not 1<=weeks<=30:continue
            if anchor.weekday()!=0 or not anchor<=start<=end or (end-anchor).days//7+1!=weeks:continue
            result[slot]={k:calendar[k] for k in ('label','term_start','term_end','week_one_monday','weeks')}
        return result
    except (OSError,ValueError,KeyError,TypeError):
        # Missing metadata must not manufacture a calendar for another semester.
        return {}
