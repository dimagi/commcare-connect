import django_tables2 as tables
from django.utils.html import format_html
from django_tables2.utils import A
from django.utils.safestring import mark_safe

class BaseTailwindTable(tables.Table):
    """Base table using Tailwind styling and custom template."""
    class Meta:
        template_name = "tailwind/base_table.html"
        attrs = {"class": "w-full text-left text-sm text-gray-600"}

class BaseTailwindTable(tables.Table):
    """Base table using Tailwind styling and custom template."""

    class Meta:
        template_name = "tailwind/base_table.html"  # Use your custom template
        attrs = {"class": "w-full text-left text-sm text-gray-600"}


class WorkerFlaggedTable(BaseTailwindTable):
    index = tables.Column(verbose_name="", orderable=False)
    time = tables.Column(verbose_name="Time")
    entityName = tables.Column(verbose_name="Entity Name")
    flags = tables.TemplateColumn(
        verbose_name="Flags",
        orderable=False,
        template_code="""
            <div class="flex relative justify-start text-sm text-brand-deep-purple font-normal w-72">
                {% if value %}
                    {% for flag in value|slice:":2" %}
                        {% include "tailwind/components/badges/badge_sm.html" with text=flag %}
                    {% endfor %}
                    {% if value|length > 2 %}
                        {% include "tailwind/components/badges/badge_sm_dropdown.html" with title='All Flags' list=value %}
                    {% endif %}
                {% endif %}
            </div>
        """,
    )
    reportIcons = tables.TemplateColumn(
        verbose_name="",
        orderable=False,
        template_code="""

            """,
    )

    class Meta:
        attrs = {
            "class": "w-full max-w-full",
            "thead": {"class": "hidden"},
            "tbody": {"class": "block w-full bg-gray-200 rounded-lg h-[400px] overflow-y-auto"},
        }
        row_attrs = {
            "class": "flex w-full justify-between gap-x-4 p-3 bg-white hover:bg-gray-100 relative transition-colors duration-300 group",
            "x-data": "{ hovered: false }",
            "@mouseenter": "hovered = true",
            "@mouseleave": "hovered = false",
        }
        sequence = ("index", "time", "entityName", "flags", "reportIcons")

    def render_index(self, value, record):
        return format_html(
            """
            <div class="flex justify-center text-sm text-brand-deep-purple font-normal w-12">
                <span x-show="!hovered && !isRowSelected({})">{}</span>
                <i x-show="hovered || isRowSelected({})"
                   :class="isRowSelected({}) ? 'fa-regular fa-square-check' : 'fa-regular fa-square'"
                   class="text-brand-deep-purple cursor-pointer"
                   x-on:click="hovered && toggleRow({})"></i>
            </div>
        """,
            value,
            value,
            value,
            value,
            value,
        )

    def render_time(self, value):
        return format_html(
            '<div class="flex justify-start text-sm text-brand-deep-purple font-normal w-28">{}</div>', value
        )

    def render_entityName(self, value):
        return format_html(
            '<div class="flex justify-start text-sm text-brand-deep-purple font-normal w-28">{}</div>', value
        )

    def render_reportIcons(self):
        return format_html(
            """
            <div class="flex relative justify-start text-sm text-brand-deep-purple font-normal w-4">
            <i class="fa-light fa-flag-swallowtail"></i>
            </div>
            """
        )


class VisitsTable(BaseTailwindTable):
    index = tables.Column(verbose_name="#", orderable=False)
    user_id = tables.Column(verbose_name="User ID")
    name = tables.Column(verbose_name="Name")
    max_visit = tables.Column(verbose_name="Max Visits")
    used_visits = tables.Column(verbose_name="Used Visits")
    end_date = tables.Column(verbose_name="End Date")

    class Meta:
        attrs = {
            "thead": {"class": "hidden"},
            "class": "",
        }
        row_attrs = {
            "class": "grid grid-cols-[30px_222px_213px_168px_172px_151px] text-slate-900 pl-5 py-4 items-center text-xs ml-1"
        }
        sequence = ("index", "user_id", "name", "max_visit", "used_visits", "end_date")

    def render_index(self, value):
        return format_html('<div class="text-brand-deep-purple">{}</div>', value)

    def render_user_id(self, value):
        return format_html("<div>{}</div>", value)

    def render_name(self, value):
        return format_html("<div>{}</div>", value)

    def render_max_visit(self, value):
        return format_html(
            """
            <div x-data='{{"originalValue": "{}", "currentValue": "{}", "hasChanged": false, "isValid": true}}'
                x-init="$watch('currentValue', value => {{
                    if (value === '') {{
                        currentValue = '0';
                        return;
                    }}
                    hasChanged = value !== originalValue;
                    isValid = !isNaN(value) && parseInt(value) >= 0;
                }})">
                <div class="flex items-center">
                    <input
                        type="text"
                        x-model="currentValue"
                        x-on:input="currentValue = $event.target.value.replace(/[^0-9]/g, '')"
                        :class="{{
                            'border border-transparent focus:border focus:border-slate-300 focus:outline-none rounded p-1.5 text-start': true,
                            'w-20': !(hasChanged && isValid),
                            'bg-indigo-100 border-none rounded-2xl w-10': hasChanged && isValid,
                        }}"
                        inputmode="numeric"
                        pattern="[0-9]*"
                        value="{}">
                </div>
            </div>
            """,
            value,
            value,
            value,
        )

    def render_used_visits(self, value):
        return format_html("<div>{}</div>", value)

    def render_end_date(self, value):
        return format_html(
            """
            <div>
                <input type="date"
                class="border focus:border-slate-300 focus:outline-none rounded w-28 p-2"
                value="{}">
            </div>
            """,
            value,
        )


class OpportunitiesListTable(tables.Table):
    index = tables.Column(verbose_name="")
    opportunity = tables.Column(verbose_name="Opportunity", orderable=False)
    entityType = tables.TemplateColumn(
        verbose_name="",
        orderable=False,
        template_code="""
            <div class="flex justify-start w-[60px] text-sm font-normal text-brand-deep-purple w-fit"
                 x-data="{
                   showTooltip: false,
                   tooltipStyle: '',
                   positionTooltip(el) {
                     const rect = el.getBoundingClientRect();
                     const top = rect.top - 30;  /* 30px above the icon */
                     const left = rect.left + rect.width/2;
                     this.tooltipStyle = `top:${top}px; left:${left}px; transform:translateX(-50%)`;
                   }
                 }">
              {% if value %}
                {% if value == 'test' %}
                    <div class="relative">
                        <i class="fa-light fa-file-dashed-line"
                           @mouseenter="showTooltip = true; positionTooltip($el)"
                           @mouseleave="showTooltip = false
                           "></i>
                        <span x-show="showTooltip"
                              :style="tooltipStyle"
                              class="fixed z-50 border bg-white text-brand-deep-purple text-xs py-0.5 px-4 rounded-lg whitespace-nowrap">
                            Test
                        </span>
                    </div>
                {% else %}
                    <span class="relative">
                        <i class="invisible fa-light fa-file-dashed-line"></i>
                    </span>
                {% endif %}
              {% endif%}
            </div>
        """,
    )
    entityStatus = tables.TemplateColumn(
        verbose_name="Status",
        orderable=False,
        template_code="""
            <div class="flex justify-center w-[178px] text-sm font-normal truncate text-brand-deep-purple overflow-clip overflow-ellipsis">
               {% if value %}
              {% if value == 'active' %}
                  {% include "tailwind/components/badges/badge_sm.html" with bg_color='green-600' bg_opacity='20' text='active' text_color='green-600' %}
              {% elif value == 'inactive' %}
                  {% include "tailwind/components/badges/badge_sm.html" with bg_color='orange-600' bg_opacity='25' text='inactive' text_color='orange-600' %}
              {% elif value == 'ended' %}
                  {% include "tailwind/components/badges/badge_sm.html" with bg_color='slate-100' bg_opacity='100' text='ended' text_color='slate-400' %}
              {% endif %}
              {% endif%}
            </div>
        """,
    )
    program = tables.Column(verbose_name="Program", orderable=False)
    startDate = tables.Column(verbose_name="Start Date", orderable=False)
    endDate = tables.Column(verbose_name="End Date", orderable=False)

    pendingInvites = tables.TemplateColumn(
        verbose_name="Pending Invites",
        orderable=False,
        template_code="""
            <div class="flex justify-center text-sm font-normal truncate text-brand-deep-purple overflow-clip overflow-ellipsis w-[126px]">
            {% if value %}
              {% include "tailwind/components/dropdowns/text_button_dropdown.html" with text=value.count list=value.list styles='text-sm' %}
            {% endif%}
            </div>
        """,
    )
    inactiveWorkers = tables.TemplateColumn(
        verbose_name="Inactive Workers",
        orderable=False,
        template_code="""
          <div class="flex justify-center w-40 text-sm font-normal truncate text-brand-deep-purple overflow-clip overflow-ellipsis">
               {% if value %}
              {% include "tailwind/components/dropdowns/text_button_dropdown.html" with text=value.count list=value.list styles='text-sm' %}
          {% endif%}
          </div>
        """,
    )
    pendingApprovals = tables.TemplateColumn(
        verbose_name="Pending Approvals",
        orderable=False,
        template_code="""
          <div class="flex justify-center w-40 text-sm font-normal truncate text-brand-deep-purple overflow-clip overflow-ellipsis">
               {% if value %}
              {% include "tailwind/components/dropdowns/text_button_dropdown.html" with text=value.count list=value.list styles='text-sm' %}
          {% endif%}
          </div>
        """,
    )
    paymentsDue = tables.TemplateColumn(
        verbose_name="Payments Due",
        orderable=False,
        template_code="""
          <div class="flex justify-center w-40 text-sm font-normal truncate text-brand-deep-purple overflow-clip overflow-ellipsis">
               {% if value %}
              {% include "tailwind/components/dropdowns/text_button_dropdown.html" with text=value.amount list=value.list styles='text-sm' %}
          {% endif%}
          </div>
        """,
    )

    class Meta:
        attrs = {"class": "w-full max-w-full", "thead": {"class": "hidden"}, "tbody": {"class": "block w-full h-full"}}
        row_attrs = {
            "class": "flex w-full justify-between gap-x-4 p-3 bg-white hover:bg-gray-100 relative transition-colors duration-300",
        }
        sequence = (
            "index",
            "opportunity",
            "entityType",
            "entityStatus",
            "program",
            "startDate",
            "endDate",
            "pendingInvites",
            "inactiveWorkers",
            "pendingApprovals",
            "paymentsDue",
        )

    def render_index(self, value):
        return format_html(
            '<div class="flex justify-center text-sm font-normal truncate text-brand-deep-purple overflow-clip overflow-ellipsis w-9">{}</div>',
            value,
        )

    def render_opportunity(self, value):
        return format_html(
            '<div class="flex justify-start w-40 text-sm font-normal truncate text-brand-deep-purple overflow-clip overflow-ellipsis">{}</div>',
            value,
        )

    def render_program(self, value):
        return format_html(
            '<div class="flex justify-start text-sm font-normal truncate text-brand-deep-purple overflow-clip overflow-ellipsis w-36">{}</div>',
            value,
        )

    def render_startDate(self, value):
        return format_html(
            '<div class="flex justify-center w-24 text-sm font-normal truncate text-brand-deep-purple overflow-clip overflow-ellipsis">{}</div>',
            value,
        )

    def render_endDate(self, value):
        return format_html(
            '<div class="flex justify-center w-24 text-sm font-normal truncate text-brand-deep-purple overflow-clip overflow-ellipsis">{}</div>',
            value,
        )


class WorkerPaymentsTable(tables.Table):
    index = tables.Column(
        orderable=False,
    )
    worker = tables.Column(
        verbose_name="Name",
    )
    indicator = tables.TemplateColumn(
        verbose_name="Indicator",
        orderable=False,
        template_code="""
                                    {% if value %}
                                       <div class="status-active"></div>
                                    {% else %}
                                        <div class="status-error"></div> 
                                    {% endif %}
                                    """,
    )
    lastActive = tables.Column(
        verbose_name="Last Active",
    )
    accrued = tables.Column(
        verbose_name="Accrued",
    )
    totalPaid = tables.Column(
        verbose_name="Total Paid",
        attrs={
            "td": {
                "class": "p-0",
            }
        },
    )
    lastPaid = tables.Column(
        verbose_name="Last Paid",
    )
    confirmed = tables.Column(
        verbose_name="Confirmed",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Custom HTML for 'select' header (your toggle button)
        self.base_columns['index'].verbose_name = mark_safe(
            '''
            <div class="flex justify-start text-sm font-medium text-brand-deep-purple">
                <i
                    x-on:click="toggleAll()"
                    :class="isAllSelected() ? 'fa-regular fa-square-check' : 'fa-regular fa-square'"
                    class="text-xl cursor-pointer text-brand-deep-purple"
                ></i>
            </div>
            '''
        )

        self.base_columns['indicator'].verbose_name = mark_safe(
            '''
                <div class="w-[40px]">
                    <div class="w-4 h-2 bg-black rounded"></div>
                </div>
            '''
        )


    class Meta:
        attrs = {
            "class": "w-full max-w-full",
            "thead": {"class": "hidden"},
            "tbody": {"class": "block w-full h-full"},
        }
        row_attrs = {
            "class": "flex text-slate-900 items-center text-xs justify-between h-14 px-3 w-full  hover:bg-gray-100 relative transition-colors duration-300"
        }
        sequence = ("index", "worker", "indicator", "lastActive", "accrued", "totalPaid", "lastPaid", "confirmed")

    def render_index(self, value, record):
        # Use 1-based indexing for display and storage
        display_index = value

        return format_html(
            """
            <div class="text-brand-deep-purple relative flex items-center justify-start h-full"
                x-data="{{
                    'hovering': false
                }}"
                x-on:mouseenter="hovering = true"
                x-on:mouseleave="hovering = false">

                <!-- Show empty square when hovering and not selected -->
                <i x-show="!isRowSelected({0}) && hovering"
                class="absolute text-xl -translate-y-1/2 cursor-pointer fa-regular fa-square text-brand-deep-purple top-1/2"
                x-on:click="toggleRow({0}); $event.stopPropagation()"></i>

                <!-- Show checked square when selected -->
                <i x-show="isRowSelected({0})"
                class="absolute text-xl -translate-y-1/2 cursor-pointer fa-regular fa-square-check text-brand-deep-purple top-1/2"
                x-on:click="toggleRow({0}); $event.stopPropagation()"></i>

                <!-- Show number when not hovering and not selected -->
                <span x-show="!isRowSelected({0}) && !hovering"
                    class="absolute pl-1 -translate-y-1/2 top-1/2">{0}</span>
            </div>
        """,
            display_index,
        )

    def render_worker(self, value):
        return format_html(
            """
        <div class="flex flex-col items-start">
            <p class="text-sm text-slate-900 ">{}</p>
            <p class="text-xs text-slate-400">{}</p>
        </div>
        """,
            value["name"],
            value["id"],
        )

    def render_lastActive(self, value):
        return format_html('<div class="">{}</div>', value)

    def render_accrued(self, value):
        return format_html('<div class="">{}</div>', value)

    def render_totalPaid(self, value):
        return format_html('<div class="">{}</div>', value)

    def render_lastPaid(self, value):
        return format_html(
            """
            <div class="relative"
                x-data="{{
                    isOpen: false,
                    positionMenu() {{
                        const rect = this.$el.getBoundingClientRect();
                        const menu = this.$refs.menu;
                        const top = rect.bottom + 5;  // Position below the element
                        const left = rect.left + rect.width/2 - menu.offsetWidth/2;
                        menu.style.top = top + 'px';
                        menu.style.left = left + 'px';
                    }}
                }}"
                x-on:click="isOpen = !isOpen; $nextTick(() => {{ if(isOpen) positionMenu() }})"
                x-on:click.outside="isOpen = false">
                <span class="px-3 py-1 rounded-lg cursor-pointer hover:bg-slate-200">
                    {}
                </span>
                <div x-ref="menu"
                    x-show="isOpen"
                    x-transition
                    class="fixed z-50 p-5 text-sm bg-white border rounded-lg shadow-md text-brand-deep-purple text-nowrap whitespace-nowrap"
                    style="display: none">
                    <p class="text-xs text-slate-400">Payment History</p>
                    <!-- TODO: @apply -->
                    <button  class="flex items-center px-2 py-2 mt-3 mb-6 text-sm font-medium border border-gray-300 rounded-lg text-brand-deep-purple hover:bg-brand-indigo-100">Rollback lastPayment</button>
                    <div hx-get='/a/test-1/opportunity/1/tw/get_worker_last_payment/' hx-trigger='load' hx-swap='outerHTML'></div>
                </div>
            </div>
        """,
            value,
        )

    def render_confirmed(self, value):
        return format_html('<div class="">{}</div>', value)

class WorkerLearnTable(tables.Table):
    index = tables.Column(
        orderable=False,
    )
    worker = tables.Column(
        verbose_name="Name",
    )
    indicator = tables.TemplateColumn(
        verbose_name="Indicator",
        orderable=False,
        template_code="""
            {% if value %}
            <div class=""><div class="w-4 h-2 rounded bg-{{ value }}"></div></div>
            {% else %}
                <div class=""><div class=" h-2"></div></div>
            {% endif %}
            """,
    )
    lastActive = tables.Column(
        verbose_name="Last Active",
    )
    start_learning = tables.Column(
        verbose_name="Start Learning",
    )
    modules_completed = tables.TemplateColumn(
        verbose_name="Modules Completed",
        template_code=""" 
                            {% include "tailwind/components/progressbar/simple-progressbar.html" with text=flag progress=value %}
                        """,
    )
    completed_learning = tables.Column(
        verbose_name="Completed Learning",
    )
    assessment = tables.Column(
        verbose_name="Assessment",
    )
    attempts = tables.Column(
        verbose_name="Attempts",
    )
    learning_hours = tables.Column(
        verbose_name="Learning Hours",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Custom HTML for 'select' header (your toggle button)
        self.base_columns['index'].verbose_name = mark_safe(
            '''
            <div class="flex justify-start text-sm font-medium text-brand-deep-purple">
                <i
                    x-on:click="toggleAll()"
                    :class="isAllSelected() ? 'fa-regular fa-square-check' : 'fa-regular fa-square'"
                    class="text-xl cursor-pointer text-brand-deep-purple"
                ></i>
            </div>
            '''
        )

        self.base_columns['indicator'].verbose_name = mark_safe(
            '''
                <div class="w-[40px]">
                    <div class="w-4 h-2 bg-black rounded"></div>
                </div>
            '''
        )

    class Meta:
        attrs = {
            "class": "w-full max-w-full",
            # "thead": {"class": "hidden"},
            "tbody": {"class": "block w-full h-full"},
        }
        row_attrs = {
            "class": "flex text-slate-900 items-center text-xs justify-between h-14 px-3 w-full  hover:bg-gray-100 relative transition-colors duration-300"
        }
        sequence = (
            "index",
            "worker",
            "indicator",
            "lastActive",
            "start_learning",
            "modules_completed",
            "completed_learning",
            "assessment",
            "attempts",
            "learning_hours",
        )
        
    def render_index(self, value, record):
        # Use 1-based indexing for display and storage
        display_index = value

        return format_html(
            """
            <div class="text-brand-deep-purple relative flex items-center justify-start w-full h-full"
                x-data="{{
                    'hovering': false
                }}"
                x-on:mouseenter="hovering = true"
                x-on:mouseleave="hovering = false">

                <!-- Show empty square when hovering and not selected -->
                <i x-show="!isRowSelected({0}) && hovering"
                class="absolute text-xl -translate-y-1/2 cursor-pointer fa-regular fa-square text-brand-deep-purple top-1/2"
                x-on:click="toggleRow({0}); $event.stopPropagation()"></i>

                <!-- Show checked square when selected -->
                <i x-show="isRowSelected({0})"
                class="absolute text-xl -translate-y-1/2 cursor-pointer fa-regular fa-square-check text-brand-deep-purple top-1/2"
                x-on:click="toggleRow({0}); $event.stopPropagation()"></i>

                <!-- Show number when not hovering and not selected -->
                <span x-show="!isRowSelected({0}) && !hovering"
                    class="absolute pl-1 -translate-y-1/2 top-1/2">{0}</span>
            </div>
        """,
            display_index,
        )

    def render_worker(self, value):
        return format_html(
            """
        <div class="flex flex-col items-start">
            <p class="text-sm text-slate-900 ">{}</p>
            <p class="text-xs text-slate-400">{}</p>
        </div>
        """,
            value["name"],
            value["id"],
        )

    def render_lastActive(self, value):
        return format_html('<div">{}</div>', value) 

    def render_start_learning(self, value):
        return format_html('<div>{}</div>', value) 
    def render_completed_learning(self, value):
        return format_html('<div class="">{}</div>', value)
    def render_assessment(self, value):
        return format_html('<div class="">{}</div>', value)
    def render_attempts(self, value):
        return format_html('<div class="">{}</div>', value)
    def render_learning_hours(self, value):
        return format_html('<div class="">{}</div>', value)

class PayWorker(BaseTailwindTable):
    index = tables.Column(verbose_name="#", orderable=False)
    worker = tables.Column(verbose_name="Worker")
    unpaid = tables.Column(verbose_name="Unpaid")
    toBePaid = tables.Column(verbose_name="To Be Paid")
    paymentDate = tables.Column(verbose_name="Payment Date")

    class Meta:
        attrs = {
            "class": "w-full max-w-full",
            "thead": {"class": "hidden"},
            "tbody": {"class": "block w-full h-full"},
        }
        row_attrs = {
            "class": "flex text-slate-900 items-center text-xs justify-between h-14 px-3 w-full  hover:bg-gray-100 relative transition-colors duration-300"
        }
        sequence = ("index", "worker", "unpaid", "toBePaid", "paymentDate")

    def render_index(self, value):
        return format_html(
            '<div class="">{}</div>', value
        )
    def render_worker(self, value):
        return format_html(
            '<div class="">{}</div>', value
        )
    def render_unpaid(self, value):
        return format_html(
            '<div class="">{}</div>', value
        )
    def render_toBePaid(self, value):
        return format_html(
            """
            <div x-data='{{"originalValue": "{}", "currentValue": "{}", "hasChanged": false, "isValid": true}}'
                x-init="$watch('currentValue', value => {{
                    if (value === '') {{
                        currentValue = '0';
                        return;
                    }}
                    hasChanged = value !== originalValue;
                    isValid = !isNaN(value) && parseInt(value) >= 0;
                }})">
                <div class="flex items-center">
                    <input
                        type="text"
                        x-model="currentValue"
                        x-on:input="currentValue = $event.target.value.replace(/[^0-9]/g, '')"
                        :class="{{
                            'border border-transparent focus:border focus:border-slate-300 focus:outline-none rounded p-1.5 text-start': true,
                            '': !(hasChanged && isValid),
                            'bg-indigo-100 border-none rounded-2xl w-fit': hasChanged && isValid,
                        }}"
                        inputmode="numeric"
                        pattern="[0-9]*"
                        value="{}">
                </div>
            </div>
            """,
            value,
            value,
            value,
        )
    def render_paymentDate(self, value):
        return format_html(
            """
            <div>
                <input type="date"
                class="border focus:border-slate-300 focus:outline-none rounded w-28 p-2"
                value="{}">
            </div>
            """,
            value,
        )
    
class WorkerMainTable(BaseTailwindTable):
    index = tables.Column(verbose_name="#", orderable=False)
    worker = tables.Column(verbose_name="Name", orderable=False)
    indicator = tables.TemplateColumn(
        verbose_name="Indicator",
        attrs={
            "td": {
                "class": "p-0",
            }
        },
        orderable=False,
        template_code="""
                                    {% if value %}
                                       <div class="w-[40px]"><div class="w-4 h-2 rounded bg-{{ value }}"></div></div>
                                    {% else %}
                                        <div class="w-[40px]"><div class="w-4 h-2"></div></div>
                                    {% endif %}
                                    """,
    )
    lastActive = tables.Column(
        verbose_name="Last Active",
        attrs={
            "td": {
                "class": "p-0",
            }
        },
    )
    inviteDate = tables.Column(
        verbose_name="Invite Date",
        attrs={
            "td": {
                "class": "p-0",
            }
        },
    )
    startedLearn = tables.Column(
        verbose_name="Started Learn",
        attrs={
            "td": {
                "class": "p-0",
            }
        },
    )
    completedLearn = tables.Column(
        verbose_name="Completed Learn",
        attrs={
            "td": {
                "class": "p-0",
            }
        },
    )
    daysToCompleteLearn = tables.Column(
        verbose_name="Days to complete Learn",
        attrs={
            "td": {
                "class": "p-0",
            }
        },
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        self.base_columns['index'].verbose_name = mark_safe(
            '''
            <div class="flex justify-start text-sm font-medium text-brand-deep-purple">
                <i
                    x-on:click="toggleAll()"
                    :class="isAllSelected() ? 'fa-regular fa-square-check' : 'fa-regular fa-square'"
                    class="text-xl cursor-pointer text-brand-deep-purple"
                ></i>
            </div>
            '''
        )

        self.base_columns['indicator'].verbose_name = mark_safe(
            '''
                <div class="w-[40px]">
                    <div class="w-4 h-2 bg-black rounded"></div>
                </div>
            '''
        )

    def render_index(self, value, record):
        display_index = value

        return format_html(
            """
            <div class="text-brand-deep-purple relative flex items-center justify-start h-full"
                x-data="{{
                    'hovering': false
                }}"
                x-on:mouseenter="hovering = true"
                x-on:mouseleave="hovering = false">

                <!-- Show empty square when hovering and not selected -->
                <i x-show="!isRowSelected({0}) && hovering"
                class="absolute text-xl -translate-y-1/2 cursor-pointer fa-regular fa-square text-brand-deep-purple top-1/2"
                x-on:click="toggleRow({0}); $event.stopPropagation()"></i>

                <!-- Show checked square when selected -->
                <i x-show="isRowSelected({0})"
                class="absolute text-xl -translate-y-1/2 cursor-pointer fa-regular fa-square-check text-brand-deep-purple top-1/2"
                x-on:click="toggleRow({0}); $event.stopPropagation()"></i>

                <!-- Show number when not hovering and not selected -->
                <span x-show="!isRowSelected({0}) && !hovering"
                    class="absolute pl-1 -translate-y-1/2 top-1/2">{0}</span>
            </div>
        """,
            display_index,
        )

    def render_worker(self, value):
        return format_html(
            """
        <div class="flex flex-col items-start">
            <p class="text-sm text-slate-900 ">{}</p>
            <p class="text-xs text-slate-400">{}</p>
        </div>
        """,
            value["name"],
            value["id"],
        )

# class WorkerDeliveryTable(BaseTailwindTable):
#     index = tables.Column(verbose_name="#", orderable=False)
#     worker = tables.Column(verbose_name="Name", orderable=False)
#     indicator = tables.TemplateColumn(
#         verbose_name="Indicator",
#         attrs={
#             "td": {
#                 "class": "p-0",
#             }
#         },
#         orderable=False,
#         template_code="""
#                                     {% if value %}
#                                        <div class="w-[40px]"><div class="w-4 h-2 rounded bg-{{ value }}"></div></div>
#                                     {% else %}
#                                         <div class="w-[40px]"><div class="w-4 h-2"></div></div>
#                                     {% endif %}
#                                     """,
#     )