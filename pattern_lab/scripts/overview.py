import sqlite3
from collections import Counter

DB = r"C:\Users\malliath\Desktop\aam_merger\pattern_lab\db\aam_merger_lab.db"
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row


def show(title, sql, params=()):
    print(f"\n=== {title}")
    for r in con.execute(sql, params):
        print("  " + " | ".join(f"{k}={r[k]}" for k in r.keys()))


show("documents by doc_type", "select doc_type, count(*) n from documents group by 1 order by n desc")
show("documents by extraction_status", "select extraction_status, count(*) n from documents group by 1 order by n desc")
show("documents by type x status", "select doc_type, extraction_status, count(*) n from documents group by 1,2 order by 1,2")
show("documents archived", "select count(*) total, sum(archived_at is not null) archived from documents")
show("po_sets by status", "select status, count(*) n from po_sets group by 1 order by n desc")
show("po_sets customs toggle", "select has_customs_toggle, customs_doc_count, count(*) n from po_sets group by 1,2 order by n desc")
show("po_sets archived", "select archived_at is null as is_open, count(*) n from po_sets group by 1")
show("lock state", "select locked_by_action, count(*) n from po_sets group by 1")
show("merge outputs", "select (merged_output_path is not null) has_path, count(*) n from po_sets group by 1")
show("po_no_source", "select po_no_source, count(*) n from documents group by 1")
show("ambiguous PO refs", "select po_reference_ambiguous, count(*) n from documents group by 1")
show("split parents", "select is_split_parent, (parent_document_id is not null) has_parent, count(*) n from documents group by 1,2")
show("line_items totals", "select count(*) rows_, sum(line_item_no is null) null_line_no, sum(quantity=0) zero_qty, sum(quantity<0) neg_qty from line_items")
show("audit_log", "select count(*) n from audit_log")
show("alembic", "select * from alembic_version")
