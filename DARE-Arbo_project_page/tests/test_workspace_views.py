from datetime import date
from io import BytesIO
import unittest
from openpyxl import Workbook
from sheet_dashboard import parse_register
from workspace_views import summary, workstream_rows, filter_activities, deadline_indicator, timeline_html, activity_key

TODAY = date(2026, 10, 30)


def task(key, status, end, progress=None, **extra):
    return dict(id=key, record_key=key, activity='Activity ' + key, workstream='Pilot',
                owner='EAA', output='Dataset', note='', assignee='', status=status,
                progress=progress, start=date(2026, 10, 1), end=end, **extra)


class WorkspaceViewTests(unittest.TestCase):
    def setUp(self):
        self.tasks = [task('A','Completed',date(2026,10,20)),
                      task('B','In progress',date(2026,10,29),25),
                      task('C','Not started',date(2026,11,6)),
                      task('D','Blocked',None),
                      task('E','In progress',date(2026,11,7),0)]

    def test_precise_metrics_and_partial_progress_coverage(self):
        self.assertEqual(dict(total=5,completed=1,active=2,overdue=1,due=1), summary(self.tasks,TODAY))
        result = workstream_rows(self.tasks,TODAY)[0]
        self.assertEqual('2 / 5',result['Progress recorded'])
        self.assertEqual(12.5,result['Mean recorded progress (%)'])
        self.assertEqual('Deadline not recorded',deadline_indicator(self.tasks[3],TODAY))
        self.assertEqual('Completed',deadline_indicator(self.tasks[0],TODAY))

    def test_filters_return_stable_identifiers(self):
        self.assertEqual(['B'],[activity_key(t) for t in filter_activities(self.tasks,dict(search='dataset',deadline='Overdue'),TODAY)])
        self.assertEqual(['D'],[t['id'] for t in filter_activities(self.tasks,dict(deadline='Blocked'),TODAY)])
        self.assertEqual([],filter_activities(self.tasks,dict(owner='Other'),TODAY))
        self.assertEqual(['C'],[t['id'] for t in filter_activities(self.tasks,dict(deadline='Due within 7 days'),TODAY)])
        self.assertEqual([],workstream_rows([],TODAY))

    def test_missing_dates_progress_and_percent_format(self):
        book=Workbook()
        sheet=book.active
        sheet.title='Activity Register'
        sheet.append(['ID','Activity','Start','End','Owner','Status','Progress (%)'])
        sheet.append(['A','Missing start','bad','2026-10-29','EAA','Not started',None])
        sheet.append(['B','Half percent','2026-10-01','2026-11-01','EAA','In progress',0.5])
        sheet.append(['C','Half completed','2026-10-01','2026-11-01','EAA','In progress',0.5])
        sheet['G4'].number_format='0%'
        out=BytesIO();book.save(out);book.close()
        tasks,issues=parse_register(out.getvalue())
        self.assertTrue(issues)
        self.assertIsNone(tasks[0]['start'])
        self.assertEqual(date(2026,10,29),tasks[0]['end'])
        self.assertIsNone(tasks[0]['progress'])
        self.assertEqual(0.5,tasks[1]['progress'])
        self.assertEqual(50,tasks[2]['progress'])

    def test_timeline_escapes_source_and_labels_dates(self):
        t={**self.tasks[1],'activity':'<script>alert(1)</script>','workstream':'"quoted"'}
        html=timeline_html([t],date(2026,10,1),date(2026,11,30),TODAY)
        self.assertNotIn('<script>',html)
        self.assertIn('&lt;script&gt;',html)
        self.assertIn('01 Oct',html)
        self.assertIn('Overdue',html)
        self.assertIn('pm-today',html)
