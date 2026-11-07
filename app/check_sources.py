"""Read-only source diagnostic. Does not open or modify the application database."""
import argparse
import os
from datetime import date, timedelta
from dotenv import load_dotenv
from .sources import get_mgsu, get_mgsu_public, SourceError
from .mgsu_pdf import get_pdf_schedule
from .mgsu_web import get_web_schedule

def main():
    load_dotenv()
    parser=argparse.ArgumentParser(description='Проверка реальных данных МГСУ без записи в базу')
    parser.add_argument('--from',dest='start',default=date.today().isoformat())
    parser.add_argument('--to',dest='end',default=(date.today()+timedelta(days=7)).isoformat())
    args=parser.parse_args()
    date.fromisoformat(args.start);date.fromisoformat(args.end)
    if args.start>args.end:parser.error('Начало периода позже окончания')
    use_api=bool(os.getenv('RASPIKA_API_KEY','').strip())
    mode=os.getenv('MGSU_SOURCE','auto').strip().lower()
    print('МГСУ · ИПГС 2-64 · подгруппа 1')
    print('Режим источника: '+mode)
    try:
        if mode in ('auto','pdf'):
            try:batch=get_pdf_schedule(args.start,args.end)
            except SourceError:
                if mode=='pdf':raise
                try:batch=get_web_schedule(args.start,args.end)
                except Exception:batch=get_mgsu(args.start,args.end) if use_api else get_mgsu_public()
        elif mode=='web':batch=get_web_schedule(args.start,args.end)
        elif mode in ('raspika','raspika_public'):batch=get_mgsu(args.start,args.end) if use_api and mode!='raspika_public' else get_mgsu_public()
        else:raise SourceError('Неизвестный MGSU_SOURCE')
    except Exception as exc:
        print(str(exc) if isinstance(exc,SourceError) else 'Источник недоступен или вернул некорректный ответ. Повторите позже.')
        return 1
    selected=[e for e in batch.events if args.start<=e['date']<=args.end]
    print(f'Период источника: {batch.coverage_from} — {batch.coverage_to}; занятий: {len(batch.events)}')
    print(f'В выбранном периоде {args.start} — {args.end}: {len(selected)} занятий')
    print(batch.label)
    if batch.note:print(batch.note)
    if args.start<batch.coverage_from or args.end>batch.coverage_to:
        print('Выбранный период покрыт не полностью. Даты вне периода источника не подтверждены; это не означает отсутствие пар.')
        return 2
    return 0

if __name__=='__main__':
    raise SystemExit(main())
