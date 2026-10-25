import sqlite3

con = sqlite3.connect(r"C:\Users\malliath\Desktop\aam_merger\pattern_lab\db\aam_merger_lab.db")
q = """select type, name, sql from sqlite_master
       where type in ('table','index','trigger') order by type, name"""
for typ, name, sql in con.execute(q):
    print(f"--- {typ}: {name}")
    print(sql)
    print()
print("alembic:", con.execute("select * from alembic_version").fetchall())
