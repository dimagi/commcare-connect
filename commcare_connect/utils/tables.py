import django_tables2 as tables


class BaseTailwindTable(tables.Table):
    # Todo; this can be set using DJANGO_TABLES2_TEMPLATE
    #   once the UI migration is complete

    class Meta:
        template_name = "tailwind/base_table.html"
