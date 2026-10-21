"""Reject missing or changed columns/types/nullability before create_all touches a live DB."""
from sqlalchemy import inspect
from app.db import Base, engine

with engine.connect() as connection:
    inspector = inspect(connection)
    problems = []
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            problems.append(table.name + ':missing_table')
            continue
        actual = {column['name']: column for column in inspector.get_columns(table.name)}
        for column in table.columns:
            item = actual.get(column.name)
            if item is None:
                problems.append(table.name + ':missing_column:' + column.name)
            elif (str(item['type'].compile(engine.dialect)) != str(column.type.compile(engine.dialect))
                  or item['nullable'] != column.nullable):
                problems.append(table.name + ':changed_column:' + column.name)
    # This check intentionally does not certify indexes, constraints, data migrations or semantic compatibility.
    print('\n'.join(problems) if problems else 'schema_columns_compatible')
    raise SystemExit(1 if problems else 0)
