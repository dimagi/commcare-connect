from django import forms
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.template import Template, Context

from commcare_connect.opportunity.forms import AddBudgetExistingUsersForm

from .tw_tables import InvoicePaymentReportTable, InvoicesListTable, MyOrganizationMembersTable, OpportunitiesListTable, OpportunityWorkerLearnProgressTable, OpportunityWorkerPaymentTable, VisitsTable, WorkerFlaggedTable, WorkerMainTable, WorkerPaymentsTable, WorkerLearnTable, PayWorker, LearnAppTable, DeliveryAppTable, PaymentAppTable, AddBudgetTable, WorkerDeliveryTable, FlaggedWorkerTable, CommonWorkerTable, AllWorkerTable



def home(request, org_slug=None, opp_id=None):
    # Static data for the cards
    rows = [
        {"name": "Data Points 1", "value": "45"},
        {"name": "Data Points 1", "value": "45"},
        {"name": "Data Points 2", "value": "45"},
        {"name": "Data Points 3", "value": "45"},
        {"name": "Data Points 4", "value": "45"},
        {"name": "Data Points 5", "value": "45"},
        {"name": "Data Points 6", "value": "45"},
        {"name": "Data Points 7", "value": "45"},
        {"name": "Data Points 8", "value": "45"},
    ]
    timeline = [
        {"title": "Event Title", "desc": "Additional Supporting Message with the Event", "date": "24 Feb, 2024"},
        {"title": "Event Title", "desc": "Additional Supporting Message with the Event", "date": "24 Feb, 2024"},
        {"title": "Event Title", "desc": "Additional Supporting Message with the Event", "date": "24 Feb, 2024"},
    ]
    flags = [
        {"title": "Location", "desc": "Minimum distance between deliveries.", "value": "2.5m"},
        {"title": "Form Duration", "desc": "Minimum time between deliveries.", "value": "10min"},
        {"title": "Photos", "desc": "Added media for proof"},
        {"title": "GPS location", "desc": "GPS location of the site is present"},
    ]
    return render(
        request,
        "tailwind/pages/home.html",
        {"rows": rows, "timeline": timeline, "flags": flags, "header_title": "Worker"},
    )


def about(request, org_slug=None, opp_id=None):
    return render(request, "tailwind/pages/about.html")

def dashboard(request, org_slug=None, opp_id=None): 
    data = {
        'programs': [
            {
                'name': 'Program Name',
                'description': 'Lorem ipsum dolor sit amet, consectetur adipiscing elit. Phasellus tempor arcu ac ligula luctus venenatis. Aliquam rhonc msan. Aliquam rhonc msan.Aliquam rhonc msan.',
                'date': '06 May, 2024',
                'organization_name': 'Program Manager Organization Name',
                'status': 'invited',
                'delivery_type': 'Name of the delivery type',
                'start_date': '12-Jul-2024',
                'end_date': '12-Jul-2024',
            },
            {
                'name': 'Program Name',
                'description': 'Lorem ipsum dolor sit amet, consectetur adipiscing elit. Phasellus tempor arcu ac ligula luctus venenatis. Aliquam rhonc msan. Aliquam rhonc msan.Aliquam rhonc msan.',
                'date': '06 May, 2024',
                'organization_name': 'Program Manager Organization Name',
                'status': 'applied',
                'delivery_type': 'Name of the delivery type',
                'start_date': '12-Jul-2024',
                'end_date': '12-Jul-2024',
            }
            ,
            {
                'name': 'Program Name',
                'description': 'Lorem ipsum dolor sit amet, consectetur adipiscing elit. Phasellus tempor arcu ac ligula luctus venenatis. Aliquam rhonc msan. Aliquam rhonc msan.Aliquam rhonc msan.',
                'date': '06 May, 2024',
                'organization_name': 'Program Manager Organization Name',
                'status': 'accepted',
                'delivery_type': 'Name of the delivery type',
                'start_date': '12-Jul-2024',
                'end_date': '12-Jul-2024',
                'opportunities_url': '/a/test-1/opportunity/1/tw/api/opportunities/'
            }
        ],
        'recent_activities': [
            {
                'name': 'Visit Pending Review',
                'icon': 'clock-rotate-left',
                'rows': [
                    {
                        'title': 'Opportunity Name',
                        'subtitle': 'Network Manager Organization Name',
                        'count': '11',
                    },
                    {
                        'title': 'Opportunity Name',
                        'subtitle': 'Network Manager Organization Name',
                        'count': '11',
                    },
                ]
            },
            {
                'name': 'Visit Pending Review',
                'icon': 'hand-holding-dollar',
                'rows': [
                    {
                        'title': 'Opportunity Name',
                        'subtitle': 'Network Manager Organization Name',
                        'count': '11',
                    },
                    {
                        'title': 'Opportunity Name',
                        'subtitle': 'Network Manager Organization Name',
                        'count': '11',
                    },
                    {
                        'title': 'Opportunity Name',
                        'subtitle': 'Network Manager Organization Name',
                        'count': '11',
                    },
                    {
                        'title': 'Opportunity Name',
                        'subtitle': 'Network Manager Organization Name',
                        'count': '11',
                    }
                ]
            },
            {
                'name': 'Inactive Workers',
                'icon': 'user-slash',
                'rows': [
                    {
                        'title': 'Opportunity Name',
                        'subtitle': 'Network Manager Organization Name',
                        'count': '11',
                    },
                    {
                        'title': 'Opportunity Name',
                        'subtitle': 'Network Manager Organization Name',
                        'count': '11',
                    },
                ]
            },

        ]
    }
    return render(
        request, 'tailwind/pages/dashboard.html', 
        {
            'data': data, 
            'header_title': 'Dashboard', 
            'sidenav_active': 'Programs'
            }
        )
    
def learn_app_table(request, org_slug=None, opp_id=None):
    data = [
        {"index": 1, "name": "Module Name 1", "description":"Additional Descriptio for module 1", "estimated_time": "1hr 30min"},
        {"index": 2, "name": "Module Name 2", "description":"Additional Descriptio for module 2", "estimated_time": "30min"},
        {"index": 3, "name": "Module Name 3", "description":"Additional Descriptio for module 3", "estimated_time": "30min"},
        {"index": 4, "name": "Module Name 4", "description":"Additional Descriptio for module 4", "estimated_time": "45min"},
        {"index": 1, "name": "Module Name 1", "description":"Additional Descriptio for module 1", "estimated_time": "1hr 30min"},
        {"index": 2, "name": "Module Name 2", "description":"Additional Descriptio for module 2", "estimated_time": "30min"},
        {"index": 3, "name": "Module Name 3", "description":"Additional Descriptio for module 3", "estimated_time": "30min"},
        {"index": 4, "name": "Module Name 4", "description":"Additional Descriptio for module 4", "estimated_time": "45min"},
        {"index": 1, "name": "Module Name 1", "description":"Additional Descriptio for module 1", "estimated_time": "1hr 30min"},
        {"index": 2, "name": "Module Name 2", "description":"Additional Descriptio for module 2", "estimated_time": "30min"},
        {"index": 3, "name": "Module Name 3", "description":"Additional Descriptio for module 3", "estimated_time": "30min"},
        {"index": 4, "name": "Module Name 4", "description":"Additional Descriptio for module 4", "estimated_time": "45min"},
        {"index": 1, "name": "Module Name 1", "description":"Additional Descriptio for module 1", "estimated_time": "1hr 30min"},
        {"index": 2, "name": "Module Name 2", "description":"Additional Descriptio for module 2", "estimated_time": "30min"},
        {"index": 3, "name": "Module Name 3", "description":"Additional Descriptio for module 3", "estimated_time": "30min"},
        {"index": 4, "name": "Module Name 4", "description":"Additional Descriptio for module 4", "estimated_time": "45min"},
        {"index": 1, "name": "Module Name 1", "description":"Additional Descriptio for module 1", "estimated_time": "1hr 30min"},
        {"index": 2, "name": "Module Name 2", "description":"Additional Descriptio for module 2", "estimated_time": "30min"},
        {"index": 3, "name": "Module Name 3", "description":"Additional Descriptio for module 3", "estimated_time": "30min"},
        {"index": 4, "name": "Module Name 4", "description":"Additional Descriptio for module 4", "estimated_time": "45min"},
        {"index": 1, "name": "Module Name 1", "description":"Additional Descriptio for module 1", "estimated_time": "1hr 30min"},
        {"index": 2, "name": "Module Name 2", "description":"Additional Descriptio for module 2", "estimated_time": "30min"},
        {"index": 3, "name": "Module Name 3", "description":"Additional Descriptio for module 3", "estimated_time": "30min"},
        {"index": 4, "name": "Module Name 4", "description":"Additional Descriptio for module 4", "estimated_time": "45min"},
        {"index": 1, "name": "Module Name 1", "description":"Additional Descriptio for module 1", "estimated_time": "1hr 30min"},
        {"index": 2, "name": "Module Name 2", "description":"Additional Descriptio for module 2", "estimated_time": "30min"},
        {"index": 3, "name": "Module Name 3", "description":"Additional Descriptio for module 3", "estimated_time": "30min"},
        {"index": 4, "name": "Module Name 4", "description":"Additional Descriptio for module 4", "estimated_time": "45min"},
    ]
    table = LearnAppTable(data)
    return render(request, 'tailwind/components/opportunity-dashboard/tables/table.html', {'table': table,'app_name':'Learn App Name'})
def delivery_app_table(request, org_slug=None, opp_id=None):
    data = [
        {"index": 1, "unit_name": "Unit Name 1", "unit_id": "Unit ID 1"},
        {"index": 2, "unit_name": "Unit Name 2", "unit_id": "Unit ID 2"},
        {"index": 3, "unit_name": "Unit Name 3", "unit_id": "Unit ID 3"},
        {"index": 4, "unit_name": "Unit Name 4", "unit_id": "Unit ID 4"},
    ]
    table = DeliveryAppTable(data)
    return render(request, 'tailwind/components/opportunity-dashboard/tables/table.html', {'table': table,'app_name':'Delivery App Name'})

def payment_app_table(request,org_slug=None, opp_id=None):
    data = [
        {"index":1, "unit_name":"Payment Unit Name 1", "start_date":"2024-01-01", "end_date":"2024-12-31", "amount":100, "total_deliveries":145, "max_daily":3, "delivery_units":"10"},
        {"index":2, "unit_name":"Payment Unit Name 2", "start_date":"2024-01-01", "end_date":"2024-12-31", "amount":100, "total_deliveries":145, "max_daily":3, "delivery_units":"10"},
        {"index":3, "unit_name":"Payment Unit Name 3", "start_date":"2024-01-01", "end_date":"2024-12-31", "amount":100, "total_deliveries":145, "max_daily":3, "delivery_units":"10"},
        {"index":4, "unit_name":"Payment Unit Name 4", "start_date":"2024-01-01", "end_date":"2024-12-31", "amount":100, "total_deliveries":145, "max_daily":3, "delivery_units":"10"},
        {"index":5, "unit_name":"Payment Unit Name 5", "start_date":"2024-01-01", "end_date":"2024-12-31", "amount":100, "total_deliveries":145, "max_daily":3, "delivery_units":"10"},
        {"index":6, "unit_name":"Payment Unit Name 6", "start_date":"2024-01-01", "end_date":"2024-12-31", "amount":100, "total_deliveries":145, "max_daily":3, "delivery_units":"10"},
        {"index":7, "unit_name":"Payment Unit Name 1", "start_date":"2024-01-01", "end_date":"2024-12-31", "amount":100, "total_deliveries":145, "max_daily":3, "delivery_units":"10"},
        {"index":8, "unit_name":"Payment Unit Name 2", "start_date":"2024-01-01", "end_date":"2024-12-31", "amount":100, "total_deliveries":145, "max_daily":3, "delivery_units":"10"},
        {"index":9, "unit_name":"Payment Unit Name 3", "start_date":"2024-01-01", "end_date":"2024-12-31", "amount":100, "total_deliveries":145, "max_daily":3, "delivery_units":"10"},
        {"index":9, "unit_name":"Payment Unit Name 4", "start_date":"2024-01-01", "end_date":"2024-12-31", "amount":100, "total_deliveries":145, "max_daily":3, "delivery_units":"10"},
        {"index":10, "unit_name":"Payment Unit Name 5", "start_date":"2024-01-01", "end_date":"2024-12-31", "amount":100, "total_deliveries":145, "max_daily":3, "delivery_units":"10"},
    ]
    table = PaymentAppTable(data)
    return render(request, 'tailwind/components/opportunity-dashboard/tables/table.html', {'table': table})

def payment_app_table_expand(request,org_slug=None, opp_id=None):
    index = request.GET.get('index')
    
    html = f'''
    <tr class="detail-row-{index}">
      <td colspan="8">
        <div class="p-3 bg-slate-100 rounded-lg">
          <div class="flex gap-16">
            <div class="flex flex-col gap-4">
              <p>Delivery Unit Name 1</p>
              <p>Delivery Unit Name 2</p>
              <p>Delivery Unit Name 3</p>
            </div>
            <div class="flex flex-col gap-4">
              <p>Delivery Unit Name 4</p>
              <p>Delivery Unit Name 5</p>
              <p>Delivery Unit Name 6</p>
            </div>
          </div>
        </div>
      </td>
    </tr>
    '''
    
    return HttpResponse(html)

def opportunities_card(request, org_slug=None, opp_id=None):
    data = [
        {
            'name': 'Oppurtunity Name',
            'description': 'Lorem ipsum dolor sit amet, consectetur adipiscing elit. Phasellus tempor arcu ac ligula luctus venenatis. Aliquam rhonc msan. Aliquam rhonc msan.Aliquam rhonc msan.',
            'date': '06 May, 2024',
            'organization_name': 'Program Manager Organization Name',
            'status': 'invited',
            'delivery_type': 'Name of the delivery type',
            'start_date': '12-Jul-2024',
            'end_date': '12-Jul-2024',
            'labels':{
                'name': 'Delieveries',
                'count': '100/150',
                'tags': [
                    {
                        'name': 'Approved',
                        'count': '70',
                        'color': 'green-600'
                    },
                    {
                        'name': 'Flagged',
                        'count': '15',
                        'color': 'brand-sunset'
                    },
                    {
                        'name': 'Rejected',
                        'count': '70',
                        'color': 'slate-400'
                    }
                ]
            }
        },
        {
            'name': 'Oppurtunity Name',
            'description': 'Lorem ipsum dolor sit amet, consectetur adipiscing elit. Phasellus tempor arcu ac ligula luctus venenatis. Aliquam rhonc msan. Aliquam rhonc msan.Aliquam rhonc msan.',
            'date': '06 May, 2024',
            'organization_name': 'Program Manager Organization Name',
            'status': 'invited',
            'delivery_type': 'Name of the delivery type',
            'start_date': '12-Jul-2024',
            'end_date': '12-Jul-2024',
            'labels':{
                'name': 'Workers',
                'count': '256',
                'tags': [
                    {
                        'name': 'Learning',
                        'count': '23',
                        'color': 'brand-mango'
                    },
                    {
                        'name': 'Claimed Job',
                        'count': '120',
                        'color': 'brand-indigo'
                    },
                    {
                        'name': 'Assessed',
                        'count': '124',
                        'color': 'green-600'
                    }
                ]
            }
        }
    ]
    return render(request, 'tailwind/components/cards/opportunity_card.html', {'data': data})

def worker(request, org_slug=None, opp_id=None):
    user_kpi = [
            {"name":"Total Delieveries", "icon":"memo","count":234,"dropdown":'nil' },
            {"name":"Flagged Delieveries", "icon":"flag-swallowtail","count":234,"dropdown":'flagged' },
            {"name":"Rejected Delieveries", "icon":"thumbs-down","count":234,"dropdown":'rejected' },
            {"name":"Accrued Payments", "icon":"money-bill-simple-wave","count":234,"dropdown":'nil' },
            {"name":"Paid Payments", "icon":"hand-holding-dollar","count":234,"dropdown":'paid' },
        ]
    flags = [{'title': 'Location','desc':'Minimum distance between deliveries.','value' : '2.5m'}, {'title': 'Form Duration','desc':'Minimum time between deliveries.','value' : '10min'},{'title': 'Photos','desc':'Added media for proof'},{'title': 'GPS location','desc':'GPS location of the site is present'}]
    flagged_info = [{'name': 'Location','correct': 5, 'wrong': 4},{'name': 'Form Duration','correct': 5, 'wrong': 4},{'name': 'Photos','correct': 5, 'wrong': 4},{'name': 'Flag Name 1','correct': 5, 'wrong': 4}]
    rejected_details = [{'name': 'location', 'count':4}, {'name': 'form duration', 'count':4},{'name': 'photos', 'count':4},{'name': 'flag name 1', 'count':4}]
    payment_details = { 'date': '24 Feb, 2024', 'amount': "$6000"}
    user_datapoints = [{'name': 'Data Points 1','value' : '45'}, {'name': 'Data Points 1','value' : '45'},{'name': 'Data Points 2','value' : '45'},{'name': 'Data Points 3','value' : '45'},{'name': 'Data Points 4','value' : '45'},{'name': 'Data Points 5','value' : '45'},{'name': 'Data Points 6','value' : '45'},{'name': 'Data Points 7','value' : '45'},{'name': 'Data Points 8','value' : '45'},]
    user_timeline = [{'title': 'Event Title','desc':'Additional Supporting Message with the Event','date' : '24 Feb, 2024'}, {'title': 'Event Title','desc':'Additional Supporting Message with the Event','date' : '24 Feb, 2024'},{'title': 'Event Title','desc':'Additional Supporting Message with the Event','date' : '24 Feb, 2024'}]
    flag_details = [{'name': 'location', 'count':4}, {'name': 'form duration', 'count':4},{'name': 'photos', 'count':4},{'name': 'flag name 1', 'count':4}]
    data = [
        {"name": "Flagged", "count": "45", "url": "/tables"},
        {"name": "PM Review", "count": "45", "url": "/tables"},
        {"name": "Revalidate", "count": "45", "url": "/tables"},
        {"name": "Approved", "count": "45", "url": "/tabls"},
        {"name": "Rejected", "count": "45", "url": "/tables"},
        {"name": "All", "count": "45", "url": "/tables"},
    ]
    return render(
        request, 
        "tailwind/pages/worker.html", 
        {
            "header_title": "Worker", 
            "tabs": data, "kpi":user_kpi , 
            "datapoints":user_datapoints, 
            "timeline":user_timeline,
            "flags":flags, 
            "flagged_info":flagged_info, 
            "rejected_details":rejected_details, 
            "payment_details":payment_details,
            "flag_details":flag_details
            }
        )
  
def opportunities(request, org_slug=None, opp_id=None):
    path = ['programs','opportunities','opportunity name' ]
    data = [
        {"name": "Learn App", "count": "2", "icon": "fa-book-open-cover"},
        {"name": "Delivery App", "count": "2", "icon": "fa-clipboard-check"},
        {"name": "Payments Units", "count": "2", "icon": "fa-hand-holding-dollar"},
    ]
    totalinfo = [
        {
            "name": "Delivery Type",
            "count": "Early Childhood Development",
            "icon": "file-check",
            "color": "",
        },
        {
            "name": "Start Date",
            "count": "21-Dec-2024",
            "icon": "calendar-range",
            "color": "",
        },
        {
            "name": "End Date",
            "count": "21-Dec-2024",
            "icon": "arrow-right",
            "color": "",
        },
        {
            "name": "Total Workers",
            "count": "248",
            "icon": "users",
            "color": "brand-mango",
        },
        {
            "name": "Total Service Deliveries",
            "count": "350",
            "icon": "gears",
            "color": "",
        },
        {
            "name": "Worker Budget",
            "count": "₹250,000",
            "icon": "money-bill",
            "color": "",
        },
    ]
    opList = [
        {
            "opName": "Workers",
            "opLabel": "Active Yesterday",
            "opValue": "10",
            "ops": [
                {"icon": "fa-user-group", "name": "Workers", "status": "Invited", "value": "25"},
                {"icon": "fa-user-check", "name": "Workers", "status": "Yet to Accept Invitation", "value": "12"},
                {
                    "icon": "fa-clipboard-list",
                    "name": "Workers",
                    "status": "Inactive last 3 days",
                    "value": "7",
                    "type": "2",
                },
            ],
        },
        {
            "opName": "Deliveries",
            "opLabel": "Last Delivery",
            "opValue": "10 Feb, 2025 | 14:67",
            "ops": [
                {
                    "icon": "fa-clipboard-list-check",
                    "name": "Deliveries",
                    "status": "Total",
                    "value": "248",
                    "incr": "6",
                },
                {
                    "icon": "fa-clipboard-list-check",
                    "name": "Deliveries",
                    "status": "Awaiting Flag Review",
                    "value": "32",
                },
            ],
        },
        {
            "opName": "Worker Payments",
            "opLabel": "Last Payment ",
            "opValue": "10 Feb, 2025 | 14:67",
            "ops": [
                {
                    "icon": "fa-hand-holding-dollar",
                    "name": "Payments",
                    "status": "Earned",
                    "value": "₹25,000",
                    "incr": "6",
                },
                {"icon": "fa-light", "name": "Payments", "status": "Due", "value": "₹1,200"},
            ],
        },
    ]
    workerporgress = [
        {"index": 1, "title":"Workers", "progress":{"total": 100, "maximum": 30,"avg":56}}, 
        {"index": 2, "title":"Deliveries", "progress":{"total": 100, "maximum": 30,"avg":56}}, 
        {"index": 3, "title":"Payments", "progress":{"total": 100, "maximum": 30,"avg":56}}, 
    ]

    funnel = [
        {"index": 1, "stage": "invited", "count": "100","icon":"envelope"},
        {"index": 2, "stage": "Accepted", "count": "98","icon":"circle-check"},
        {"index": 3, "stage": "Started Learning", "count": "87","icon":"book-open-cover"},
        {"index": 4, "stage": "Completed Learning", "count": "82","icon":"book-blank"},
        {"index": 5, "stage": "Complted Assesment", "count": "81","icon":"award-simple"},
        {"index": 6, "stage": "Claimed Job", "count": "100","icon":"user-check"},
        {"index": 6, "stage": "Started Delivery", "count": "100","icon":"house-chimney-user"},
    ]
    return render(
        request,
        "tailwind/pages/opportunities.html",
        {
            "data": data,
            "totalinfo": totalinfo,
            "opList": opList,
            "header_title": "Opportunities",
            "funnel":funnel,
            "workerprogress":workerporgress,
            'path':path
        },
    )


def flagged_workers(request, org_slug=None, opp_id=None):
    # Sample dynamic data (replace with your actual data source later)
    data = [
        {
            "index": 1,
            "time": "14:56",
            "entityName": "Violla Maeya",
            "flags": ["Location", "Form Duration", "Photos", "Flag Name 1", "Flag Name 2", "Flag Name 3"],
        },
        {"index": 2, "time": "14:57", "entityName": "John Doe", "flags": ["Location", "Photos"]},
        {
            "index": 3,
            "time": "14:58",
            "entityName": "Jane Smith",
            "flags": ["Form Duration", "Flag Name 1", "Flag Name 2"],
        },
        {"index": 4, "time": "14:59", "entityName": "Alex Brown", "flags": []},
        {"index": 5, "time": "15:00", "entityName": "Violla Maeya", "flags": ["Location", "Form Duration", "Photos"]},
        {
            "index": 11,
            "time": "14:56",
            "entityName": "Violla Maeya",
            "flags": ["Location", "Form Duration", "Photos", "Flag Name 1", "Flag Name 2", "Flag Name 3"],
        },
        {"index": 12, "time": "14:57", "entityName": "John Doe", "flags": ["Location", "Photos"]},
        {
            "index": 13,
            "time": "14:58",
            "entityName": "Jane Smith",
            "flags": ["Form Duration", "Flag Name 1", "Flag Name 2"],
        },
        {"index": 14, "time": "14:59", "entityName": "Alex Brown", "flags": []},
        {"index": 15, "time": "15:00", "entityName": "Violla Maeya", "flags": ["Location", "Form Duration", "Photos"]},
        {
            "index": 21,
            "time": "14:56",
            "entityName": "Violla Maeya",
            "flags": ["Location", "Form Duration", "Photos", "Flag Name 1", "Flag Name 2", "Flag Name 3"],
        },
        {"index": 22, "time": "14:57", "entityName": "John Doe", "flags": ["Location", "Photos"]},
        {
            "index": 23,
            "time": "14:58",
            "entityName": "Jane Smith",
            "flags": ["Form Duration", "Flag Name 1", "Flag Name 2"],
        },
        {"index": 24, "time": "14:59", "entityName": "Alex Brown", "flags": []},
        {"index": 25, "time": "15:00", "entityName": "Violla Maeya", "flags": ["Location", "Form Duration", "Photos"]},
        {
            "index": 31,
            "time": "14:56",
            "entityName": "Violla Maeya",
            "flags": ["Location", "Form Duration", "Photos", "Flag Name 1", "Flag Name 2", "Flag Name 3"],
        },
        {"index": 32, "time": "14:57", "entityName": "John Doe", "flags": ["Location", "Photos"]},
        {
            "index": 33,
            "time": "14:58",
            "entityName": "Jane Smith",
            "flags": ["Form Duration", "Flag Name 1", "Flag Name 2"],
        },
        {"index": 34, "time": "14:59", "entityName": "Alex Brown", "flags": []},
        {"index": 35, "time": "15:00", "entityName": "Violla Maeya", "flags": ["Location", "Form Duration", "Photos"]},
    ]
    table = WorkerFlaggedTable(data)
    return render(request, "tailwind/components/tables/worker_flagged_table.html", {"table": table})


class TWAddBudgetExistingUsersForm(AddBudgetExistingUsersForm):
    additional_visits = forms.IntegerField(
        widget=forms.NumberInput(
            attrs={
                "class": "w-full p-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500",
                "x-model": "additionalVisits",
            }
        ),
        required=False,
    )

    end_date = forms.DateField(
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "w-full p-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500",
                "x-model": "end_date",
            }
        ),
        label="Extended Opportunity End date",
        required=False,
    )

    def __init__(self, *args, **kwargs):
        opportunity_claims = kwargs.pop("opportunity_claims", [])
        self.opportunity = kwargs.pop("opportunity", None)
        super().__init__(*args, **kwargs)

        choices = [(opp_claim.id, opp_claim.id) for opp_claim in opportunity_claims]
        self.fields["selected_users"] = forms.MultipleChoiceField(
            choices=choices, widget=forms.CheckboxSelectMultiple(attrs={"class": "block space-y-2"})
        )


def opportunity_visits(request, org_slug=None, opp_id=None):
    from commcare_connect.opportunity.models import OpportunityAccess, OpportunityClaim
    from commcare_connect.opportunity.views import get_opportunity_or_404

    opportunity = get_opportunity_or_404(org_slug=org_slug, pk=opp_id)
    opportunity_access = OpportunityAccess.objects.filter(opportunity=opportunity)
    opportunity_claims = OpportunityClaim.objects.filter(opportunity_access__in=opportunity_access)

    form = TWAddBudgetExistingUsersForm(
        opportunity_claims=opportunity_claims, opportunity=opportunity, data=request.POST or None
    )
    if form.is_valid():
        form.save()
        return redirect("opportunity:detail", org_slug, opp_id)

    data = [
        {
            "index": 1,
            "user_id": "AB12CD34EF56",
            "name": "John Doe",
            "max_visit": 120,
            "used_visits": 89,
            "end_date": "2025-07-12",
        },
        {
            "index": 2,
            "user_id": "XY98GH76IJ54",
            "name": "Alice Smith",
            "max_visit": 150,
            "used_visits": 45,
            "end_date": "2025-09-30",
        },
        {
            "index": 3,
            "user_id": "MN45KL89OP12",
            "name": "Bob Johnson",
            "max_visit": 100,
            "used_visits": 72,
            "end_date": "2026-02-15",
        },
        {
            "index": 4,
            "user_id": "QR23ST56UV78",
            "name": "Emma Wilson",
            "max_visit": 180,
            "used_visits": 160,
            "end_date": "2025-11-20",
        },
        {
            "index": 5,
            "user_id": "WX67YZ12AB34",
            "name": "Michael Brown",
            "max_visit": 90,
            "used_visits": 25,
            "end_date": "2026-05-10",
        },
        {
            "index": 6,
            "user_id": "KL34MN78OP56",
            "name": "Sophia Martinez",
            "max_visit": 130,
            "used_visits": 98,
            "end_date": "2025-08-21",
        },
        {
            "index": 7,
            "user_id": "UV12WX34YZ56",
            "name": "James Anderson",
            "max_visit": 170,
            "used_visits": 140,
            "end_date": "2026-04-05",
        },
        {
            "index": 8,
            "user_id": "CD78EF12GH34",
            "name": "Olivia Taylor",
            "max_visit": 200,
            "used_visits": 180,
            "end_date": "2025-12-25",
        },
        {
            "index": 9,
            "user_id": "IJ45KL67MN89",
            "name": "William Harris",
            "max_visit": 95,
            "used_visits": 50,
            "end_date": "2025-06-18",
        },
        {
            "index": 10,
            "user_id": "OP23QR45ST67",
            "name": "Charlotte White",
            "max_visit": 160,
            "used_visits": 130,
            "end_date": "2026-07-01",
        },
        {
            "index": 11,
            "user_id": "EF12GH34IJ56",
            "name": "Benjamin Lewis",
            "max_visit": 110,
            "used_visits": 85,
            "end_date": "2025-10-11",
        },
        {
            "index": 12,
            "user_id": "ST78UV12WX34",
            "name": "Mia Scott",
            "max_visit": 140,
            "used_visits": 95,
            "end_date": "2026-03-30",
        },
        {
            "index": 13,
            "user_id": "YZ45AB67CD89",
            "name": "Elijah Hall",
            "max_visit": 180,
            "used_visits": 170,
            "end_date": "2025-09-09",
        },
        {
            "index": 14,
            "user_id": "GH23IJ45KL67",
            "name": "Amelia Young",
            "max_visit": 125,
            "used_visits": 110,
            "end_date": "2026-01-25",
        },
        {
            "index": 15,
            "user_id": "MN78OP12QR34",
            "name": "Lucas King",
            "max_visit": 190,
            "used_visits": 150,
            "end_date": "2025-11-15",
        },
        {
            "index": 16,
            "user_id": "WX45YZ67AB89",
            "name": "Harper Wright",
            "max_visit": 105,
            "used_visits": 70,
            "end_date": "2026-06-20",
        },
        {
            "index": 17,
            "user_id": "CD12EF34GH56",
            "name": "Henry Green",
            "max_visit": 175,
            "used_visits": 160,
            "end_date": "2025-12-01",
        },
        {
            "index": 18,
            "user_id": "IJ78KL12MN34",
            "name": "Evelyn Adams",
            "max_visit": 115,
            "used_visits": 90,
            "end_date": "2026-05-17",
        },
        {
            "index": 19,
            "user_id": "OP45QR67ST89",
            "name": "Alexander Nelson",
            "max_visit": 145,
            "used_visits": 120,
            "end_date": "2025-08-05",
        },
        {
            "index": 20,
            "user_id": "UV23WX45YZ67",
            "name": "Isabella Carter",
            "max_visit": 135,
            "used_visits": 100,
            "end_date": "2026-02-22",
        },
    ]

    table = VisitsTable(data)
    return render(
        request,
        "tailwind/pages/opportunity_visits.html",
        {
            "table": table,
            "form": form,
            "opportunity_claims": opportunity_claims,
            "budget_per_visit": opportunity.budget_per_visit_new,
            "opportunity": opportunity,
        },
    )

def add_budget(request, org_slug=None, opp_id=None):
    data = [
        {
            "index": 1,
            "user_id": "AB12CD34EF56",
            "name": "John Doe",
            "max_visit": 120,
            "used_visits": 89,
            "end_date": "2025-07-12",
        },
        {
            "index": 2,
            "user_id": "XY98GH76IJ54",
            "name": "Alice Smith",
            "max_visit": 150,
            "used_visits": 45,
            "end_date": "2025-09-30",
        },
        {
            "index": 3,
            "user_id": "MN45KL89OP12",
            "name": "Bob Johnson",
            "max_visit": 100,
            "used_visits": 72,
            "end_date": "2026-02-15",
        },
        {
            "index": 4,
            "user_id": "QR23ST56UV78",
            "name": "Emma Wilson",
            "max_visit": 180,
            "used_visits": 160,
            "end_date": "2025-11-20",
        },
        {
            "index": 5,
            "user_id": "WX67YZ12AB34",
            "name": "Michael Brown",
            "max_visit": 90,
            "used_visits": 25,
            "end_date": "2026-05-10",
        },
        {
            "index": 6,
            "user_id": "KL34MN78OP56",
            "name": "Sophia Martinez",
            "max_visit": 130,
            "used_visits": 98,
            "end_date": "2025-08-21",
        },
        {
            "index": 7,
            "user_id": "UV12WX34YZ56",
            "name": "James Anderson",
            "max_visit": 170,
            "used_visits": 140,
            "end_date": "2026-04-05",
        },
        {
            "index": 8,
            "user_id": "CD78EF12GH34",
            "name": "Olivia Taylor",
            "max_visit": 200,
            "used_visits": 180,
            "end_date": "2025-12-25",
        },
        {
            "index": 9,
            "user_id": "IJ45KL67MN89",
            "name": "William Harris",
            "max_visit": 95,
            "used_visits": 50,
            "end_date": "2025-06-18",
        },
        {
            "index": 10,
            "user_id": "OP23QR45ST67",
            "name": "Charlotte White",
            "max_visit": 160,
            "used_visits": 130,
            "end_date": "2026-07-01",
        },
        {
            "index": 11,
            "user_id": "EF12GH34IJ56",
            "name": "Benjamin Lewis",
            "max_visit": 110,
            "used_visits": 85,
            "end_date": "2025-10-11",
        },
        {
            "index": 12,
            "user_id": "ST78UV12WX34",
            "name": "Mia Scott",
            "max_visit": 140,
            "used_visits": 95,
            "end_date": "2026-03-30",
        },
        {
            "index": 13,
            "user_id": "YZ45AB67CD89",
            "name": "Elijah Hall",
            "max_visit": 180,
            "used_visits": 170,
            "end_date": "2025-09-09",
        },
        {
            "index": 14,
            "user_id": "GH23IJ45KL67",
            "name": "Amelia Young",
            "max_visit": 125,
            "used_visits": 110,
            "end_date": "2026-01-25",
        },
        {
            "index": 15,
            "user_id": "MN78OP12QR34",
            "name": "Lucas King",
            "max_visit": 190,
            "used_visits": 150,
            "end_date": "2025-11-15",
        },
        {
            "index": 16,
            "user_id": "WX45YZ67AB89",
            "name": "Harper Wright",
            "max_visit": 105,
            "used_visits": 70,
            "end_date": "2026-06-20",
        },
        {
            "index": 17,
            "user_id": "CD12EF34GH56",
            "name": "Henry Green",
            "max_visit": 175,
            "used_visits": 160,
            "end_date": "2025-12-01",
        },
        {
            "index": 18,
            "user_id": "IJ78KL12MN34",
            "name": "Evelyn Adams",
            "max_visit": 115,
            "used_visits": 90,
            "end_date": "2026-05-17",
        },
        {
            "index": 19,
            "user_id": "OP45QR67ST89",
            "name": "Alexander Nelson",
            "max_visit": 145,
            "used_visits": 120,
            "end_date": "2025-08-05",
        },
        {
            "index": 20,
            "user_id": "UV23WX45YZ67",
            "name": "Isabella Carter",
            "max_visit": 135,
            "used_visits": 100,
            "end_date": "2026-02-22",
        },
    ]

    table = AddBudgetTable(data)
    return render(
        request,
        "tailwind/pages/add_budget.html",
        {
            "title": "Add Budget",
            "table": table,
            "opportunity_name": "Opportunity Name"
        },
    )
def opportunities_list_table_view(request, org_slug=None, opp_id=None):
    data = [
    {
        "index": 1,
        "opportunity": "Opportunity Name",
        "entityType": "live",
        "entityStatus": "active",
        "program": "Program Name",
        "startDate": "12 Jul, 2025",
        "endDate": "12 Aug, 2025",
        "pendingInvites": {"count": 76, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "inactiveWorkers": {"count": 44, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "pendingApprovals": {"count": 56, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "paymentsDue": {"amount": "$123", "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "actions": {"list": ["View Opportunity", "View Worker", "View Invoices"]},
    },
    {
        "index": 2,
        "opportunity": "Opportunity Name",
        "entityType": "test",
        "entityStatus": "active",
        "program": "Program Name",
        "startDate": "12 Jul, 2025",
        "endDate": "12 Aug, 2025",
        "pendingInvites": {"count": 76, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "inactiveWorkers": {"count": 44, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "pendingApprovals": {"count": 56, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "paymentsDue": {"amount": "$123", "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "actions": {"list": ["View Opportunity", "View Worker", "View Invoices"]}
    },
    {
        "index": 3,
        "opportunity": "Opportunity Name",
        "entityType": "live",
        "entityStatus": "inactive",
        "program": "Program Name",
        "startDate": "12 Jul, 2025",
        "endDate": "12 Aug, 2025",
        "pendingInvites": {"count": 76, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "inactiveWorkers": {"count": 44, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "pendingApprovals": {"count": 56, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "paymentsDue": {"amount": "$123", "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "actions": {"list": ["View Opportunity", "View Worker", "View Invoices"]}
    },
    {
        "index": 4,
        "opportunity": "Opportunity Name",
        "entityType": "test",
        "entityStatus": "ended",
        "program": "Program Name",
        "startDate": "12 Jul, 2025",
        "endDate": "12 Aug, 2025",
        "pendingInvites": {"count": 76, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "inactiveWorkers": {"count": 44, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "pendingApprovals": {"count": 56, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "paymentsDue": {"amount": "$123", "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "actions": {"list": ["View Opportunity", "View Worker", "View Invoices"]}
    },
    {
        "index": 5,
        "opportunity": "Opportunity Name",
        "entityType": "live",
        "entityStatus": "inactive",
        "program": "Program Name",
        "startDate": "12 Jul, 2025",
        "endDate": "12 Aug, 2025",
        "pendingInvites": {"count": 76, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "inactiveWorkers": {"count": 44, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "pendingApprovals": {"count": 56, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "paymentsDue": {"amount": "$123", "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "actions": {"list": ["View Opportunity", "View Worker", "View Invoices"]}
    },
    {
        "index": 6,
        "opportunity": "Opportunity Name",
        "entityType": "test",
        "entityStatus": "active",
        "program": "Program Name",
        "startDate": "12 Jul, 2025",
        "endDate": "12 Aug, 2025",
        "pendingInvites": {"count": 76, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "inactiveWorkers": {"count": 44, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "pendingApprovals": {"count": 56, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "paymentsDue": {"amount": "$123", "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "actions": {"list": ["View Opportunity", "View Worker", "View Invoices"]}
    },
    {
        "index": 7,
        "opportunity": "Opportunity Name",
        "entityType": "live",
        "entityStatus": "ended",
        "program": "Program Name",
        "startDate": "12 Jul, 2025",
        "endDate": "12 Aug, 2025",
        "pendingInvites": {"count": 76, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "inactiveWorkers": {"count": 44, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "pendingApprovals": {"count": 56, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "paymentsDue": {"amount": "$123", "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "actions": {"list": ["View Opportunity", "View Worker", "View Invoices"]}
    },
    {
        "index": 8,
        "opportunity": "Opportunity Name",
        "entityType": "test",
        "entityStatus": "ended",
        "program": "Program Name",
        "startDate": "12 Jul, 2025",
        "endDate": "12 Aug, 2025",
        "pendingInvites": {"count": 76, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "inactiveWorkers": {"count": 44, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "pendingApprovals": {"count": 56, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "paymentsDue": {"amount": "$123", "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "actions": {"list": ["View Opportunity", "View Worker", "View Invoices"]}
    },
    {
        "index": 9,
        "opportunity": "Opportunity Name",
        "entityType": "live",
        "entityStatus": "inactive",
        "program": "Program Name",
        "startDate": "12 Jul, 2025",
        "endDate": "12 Aug, 2025",
        "pendingInvites": {"count": 76, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "inactiveWorkers": {"count": 44, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "pendingApprovals": {"count": 56, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "paymentsDue": {"amount": "$123", "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "actions": {"list": ["View Opportunity", "View Worker", "View Invoices"]}
    },
    {
        "index": 10,
        "opportunity": "Opportunity Name",
        "entityType": "test",
        "entityStatus": "active",
        "program": "Program Name",
        "startDate": "12 Jul, 2025",
        "endDate": "12 Aug, 2025",
        "pendingInvites": {"count": 76, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "inactiveWorkers": {"count": 44, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "pendingApprovals": {"count": 56, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "paymentsDue": {"amount": "$123", "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "actions": {"list": ["View Opportunity", "View Worker", "View Invoices"]}
    },
    {
        "index": 11,
        "opportunity": "Opportunity Name",
        "entityType": "live",
        "entityStatus": "ended",
        "program": "Program Name",
        "startDate": "12 Jul, 2025",
        "endDate": "12 Aug, 2025",
        "pendingInvites": {"count": 76, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "inactiveWorkers": {"count": 44, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "pendingApprovals": {"count": 56, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "paymentsDue": {"amount": "$123", "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "actions": {"list": ["View Opportunity", "View Worker", "View Invoices"]}
    },
    {
        "index": 12,
        "opportunity": "Opportunity Name",
        "entityType": "test",
        "entityStatus": "inactive",
        "program": "Program Name",
        "startDate": "12 Jul, 2025",
        "endDate": "12 Aug, 2025",
        "pendingInvites": {"count": 76, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "inactiveWorkers": {"count": 44, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "pendingApprovals": {"count": 56, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "paymentsDue": {"amount": "$123", "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "actions": {"list": ["View Opportunity", "View Worker", "View Invoices"]}
    },
    {
        "index": 13,
        "opportunity": "Opportunity Name",
        "entityType": "live",
        "entityStatus": "inactive",
        "program": "Program Name",
        "startDate": "12 Jul, 2025",
        "endDate": "12 Aug, 2025",
        "pendingInvites": {"count": 76, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "inactiveWorkers": {"count": 44, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "pendingApprovals": {"count": 56, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "paymentsDue": {"amount": "$123", "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "actions": {"list": ["View Opportunity", "View Worker", "View Invoices"]}
    },
    {
        "index": 14,
        "opportunity": "Opportunity Name",
        "entityType": "test",
        "entityStatus": "active",
        "program": "Program Name",
        "startDate": "12 Jul, 2025",
        "endDate": "12 Aug, 2025",
        "pendingInvites": {"count": 76, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "inactiveWorkers": {"count": 44, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "pendingApprovals": {"count": 56, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "paymentsDue": {"amount": "$123", "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "actions": {"list": ["View Opportunity", "View Worker", "View Invoices"]}
    },
    {
        "index": 15,
        "opportunity": "Opportunity Name",
        "entityType": "live",
        "entityStatus": "ended",
        "program": "Program Name",
        "startDate": "12 Jul, 2025",
        "endDate": "12 Aug, 2025",
        "pendingInvites": {"count": 76, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "inactiveWorkers": {"count": 44, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "pendingApprovals": {"count": 56, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "paymentsDue": {"amount": "$123", "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "actions": {"list": ["View Opportunity", "View Worker", "View Invoices"]}
    },
    {
        "index": 16,
        "opportunity": "Opportunity Name",
        "entityType": "test",
        "entityStatus": "ended",
        "program": "Program Name",
        "startDate": "12 Jul, 2025",
        "endDate": "12 Aug, 2025",
        "pendingInvites": {"count": 76, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "inactiveWorkers": {"count": 44, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "pendingApprovals": {"count": 56, "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "paymentsDue": {"amount": "$123", "list": ["View Opportunity", "View Worker", "View Invoices"], "link":"#"},
        "actions": {"list": ["View Opportunity", "View Worker", "View Invoices"]}
    },
    {
        "index": 17,
        "opportunity": "Opportunity Name",
        "entityType": "live",
        "entityStatus": "active",
        "program": "Program Name",
        "startDate": "12 Jul, 2025",
        "endDate": "12 Aug, 2025",
        "pendingInvites": {"count": 76, "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "inactiveWorkers": {"count": 44, "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "pendingApprovals": {"count": 56, "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "paymentsDue": {"amount": "$123", "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "actions": {"list": ["View Opportunity", "View Worker", "View Invoices"]}
    },
    {
        "index": 18,
        "opportunity": "Opportunity Name",
        "entityType": "test",
        "entityStatus": "inactive",
        "program": "Program Name",
        "startDate": "12 Jul, 2025",
        "endDate": "12 Aug, 2025",
        "pendingInvites": {"count": 76, "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "inactiveWorkers": {"count": 44, "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "pendingApprovals": {"count": 56, "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "paymentsDue": {"amount": "$123", "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "actions": {"list": ["View Opportunity", "View Worker", "View Invoices"]}
    },
    {
        "index": 19,
        "opportunity": "Opportunity Name",
        "entityType": "live",
        "entityStatus": "active",
        "program": "Program Name",
        "startDate": "12 Jul, 2025",
        "endDate": "12 Aug, 2025",
        "pendingInvites": {"count": 76, "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "inactiveWorkers": {"count": 44, "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "pendingApprovals": {"count": 56, "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "paymentsDue": {"amount": "$123", "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "actions": {"list": ["View Opportunity", "View Worker", "View Invoices"]}
    },
    {
        "index": 20,
        "opportunity": "Opportunity Name",
        "entityType": "test",
        "entityStatus": "ended",
        "program": "Program Name",
        "startDate": "12 Jul, 2025",
        "endDate": "12 Aug, 2025",
        "pendingInvites": {"count": 76, "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "inactiveWorkers": {"count": 44, "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "pendingApprovals": {"count": 56, "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "paymentsDue": {"amount": "$123", "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "actions": {"list": ["View Opportunity", "View Worker", "View Invoices"]}
    },
    {
        "index": 21,
        "opportunity": "Opportunity Name",
        "entityType": "live",
        "entityStatus": "inactive",
        "program": "Program Name",
        "startDate": "12 Jul, 2025",
        "endDate": "12 Aug, 2025",
        "pendingInvites": {"count": 76, "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "inactiveWorkers": {"count": 44, "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "pendingApprovals": {"count": 56, "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "paymentsDue": {"amount": "$123", "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "actions": {"list": ["View Opportunity", "View Worker", "View Invoices"]}
    },
    {
        "index": 22,
        "opportunity": "Opportunity Name",
        "entityType": "test",
        "entityStatus": "active",
        "program": "Program Name",
        "startDate": "12 Jul, 2025",
        "endDate": "12 Aug, 2025",
        "pendingInvites": {"count": 76, "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "inactiveWorkers": {"count": 44, "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "pendingApprovals": {"count": 56, "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "paymentsDue": {"amount": "$123", "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "actions": {"list": ["View Opportunity", "View Worker", "View Invoices"]}
    },
    {
        "index": 23,
        "opportunity": "Opportunity Name",
        "entityType": "live",
        "entityStatus": "ended",
        "program": "Program Name",
        "startDate": "12 Jul, 2025",
        "endDate": "12 Aug, 2025",
        "pendingInvites": {"count": 76, "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "inactiveWorkers": {"count": 44, "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "pendingApprovals": {"count": 56, "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "paymentsDue": {"amount": "$123", "list": ["View Opportunity", "View Worker", "View Invoices"], "link": "#"},
        "actions": {"list": ["View Opportunity", "View Worker", "View Invoices"]}
    }
]

    # data = []
    if len(data) == 0:
        return render(request, "tailwind/components/placeholders/opportunities_list_table_placeholder.html")

    table = OpportunitiesListTable(data)
    return render(request, "tailwind/components/tables/opportunities_list_table-backup.html", {"table": table})


def opportunities_list(request, org_slug=None, opp_id=None):
    return render(
        request,
        "tailwind/pages/opportunities_list.html",
        {"header_title": "Opportunities List"},
    )

def worker_payments(request, org_slug=None, opp_id=None):
    data = [
        {
            "index": 1,
            "worker": {"id": "UV23WX45YZ67", "name": "Isabella Carter"},
            "indicator": "orange-600",
            "lastActive": "9hr ago",
            "accrued": "₹ 4,780",
            "totalPaid": "₹ 4,780",
            "lastPaid": "12-Aug-2025",
            "confirmed": "₹ 4,780",
        },
        {
            "index": 2,
            "worker": {"id": "OP45QR67ST89", "name": "Alexander Nelson"},
            "indicator": "green-600",
            "lastActive": "9hr ago",
            "accrued": "₹ 4,780",
            "totalPaid": "₹ 4,780",
            "lastPaid": "12-Aug-2025",
            "confirmed": "₹ 4,780",
        },
        {
            "index": 3,
            "worker": {"id": "IJ78KL12MN34", "name": "Evelyn Adams"},
            "indicator": "red-600",
            "lastActive": "95 hr ago",
            "accrued": "₹ 4,780",
            "totalPaid": "₹ 4,780",
            "lastPaid": "12-Aug-2025",
            "confirmed": "₹ 4,780",
        },
        {
            "index": 4,
            "worker": {"id": "AB23CD45EF67", "name": "Liam Parker"},
            "lastActive": "3hr ago",
            "accrued": "₹ 5,000",
            "totalPaid": "₹ 5,000",
            "lastPaid": "13-Aug-2025",
            "confirmed": "₹ 5,000",
        },
        {
            "index": 5,
            "worker": {"id": "GH56IJ78KL90", "name": "Olivia Robinson"},
            "indicator": "yellow-600",
            "lastActive": "12hr ago",
            "accrued": "₹ 6,200",
            "totalPaid": "₹ 6,200",
            "lastPaid": "14-Aug-2025",
            "confirmed": "₹ 6,200",
        },
        {
            "index": 6,
            "worker": {"id": "MN23OP45QR67", "name": "Noah Martinez"},
            "indicator": "gray-600",
            "lastActive": "24hr ago",
            "accrued": "₹ 3,500",
            "totalPaid": "₹ 3,500",
            "lastPaid": "12-Aug-2025",
            "confirmed": "₹ 3,500",
        },
        {
            "index": 7,
            "worker": {"id": "ST89UV12WX34", "name": "Emma Wilson"},
            "lastActive": "48hr ago",
            "accrued": "₹ 4,000",
            "totalPaid": "₹ 4,000",
            "lastPaid": "15-Aug-2025",
            "confirmed": "₹ 4,000",
        },
        {
            "index": 8,
            "worker": {"id": "YZ12AB34CD56", "name": "James Smith"},
            "indicator": "red-600",
            "lastActive": "72hr ago",
            "accrued": "₹ 5,500",
            "totalPaid": "₹ 5,500",
            "lastPaid": "16-Aug-2025",
            "confirmed": "₹ 5,500",
        },
        {
            "index": 9,
            "worker": {"id": "EF78GH90IJ12", "name": "Sophia Johnson"},
            "indicator": "orange-600",
            "lastActive": "24hr ago",
            "accrued": "₹ 4,300",
            "totalPaid": "₹ 4,300",
            "lastPaid": "17-Aug-2025",
            "confirmed": "₹ 4,300",
        },
        {
            "index": 10,
            "worker": {"id": "KL34MN56OP78", "name": "Mason Taylor"},
            "indicator": "green-600",
            "lastActive": "96hr ago",
            "accrued": "₹ 5,000",
            "totalPaid": "₹ 5,000",
            "lastPaid": "18-Aug-2025",
            "confirmed": "₹ 5,000",
        },
        {
            "index": 11,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "accrued": "₹ 6,000",
            "totalPaid": "₹ 6,000",
            "lastPaid": "19-Aug-2025",
            "confirmed": "₹ 6,000",
        },
        {
            "index": 12,
            "worker": {"id": "WX78YZ90AB12", "name": "Lucas Harris"},
            "lastActive": "15hr ago",
            "accrued": "₹ 4,100",
            "totalPaid": "₹ 4,100",
            "lastPaid": "20-Aug-2025",
            "confirmed": "₹ 4,100",
        },
        {
            "index": 13,
            "worker": {"id": "CD56EF78GH90", "name": "Charlotte Garcia"},
            "lastActive": "8hr ago",
            "accrued": "₹ 5,500",
            "totalPaid": "₹ 5,500",
            "lastPaid": "21-Aug-2025",
            "confirmed": "₹ 5,500",
        },
        {
            "index": 14,
            "worker": {"id": "IJ34KL56MN78", "name": "Henry Lee"},
            "indicator": "red-600",
            "lastActive": "72hr ago",
            "accrued": "₹ 3,800",
            "totalPaid": "₹ 3,800",
            "lastPaid": "22-Aug-2025",
            "confirmed": "₹ 3,800",
        },
        {
            "index": 15,
            "worker": {"id": "OP90QR12ST34", "name": "Grace Scott"},
            "indicator": "gray-600",
            "lastActive": "60hr ago",
            "accrued": "₹ 6,200",
            "totalPaid": "₹ 6,200",
            "lastPaid": "23-Aug-2025",
            "confirmed": "₹ 6,200",
        },
        {
            "index": 16,
            "worker": {"id": "UV12WX34YZ56", "name": "David Martinez"},
            "indicator": "orange-600",
            "lastActive": "5hr ago",
            "accrued": "₹ 4,800",
            "totalPaid": "₹ 4,800",
            "lastPaid": "24-Aug-2025",
            "confirmed": "₹ 4,800",
        },
        {
            "index": 17,
            "worker": {"id": "AB34CD56EF78", "name": "Lily Robinson"},
            "indicator": "yellow-600",
            "lastActive": "28hr ago",
            "accrued": "₹ 5,200",
            "totalPaid": "₹ 5,200",
            "lastPaid": "25-Aug-2025",
            "confirmed": "₹ 5,200",
        },
        {
            "index": 18,
            "worker": {"id": "GH90IJ12KL34", "name": "Benjamin King"},
            "lastActive": "11hr ago",
            "accrued": "₹ 5,500",
            "totalPaid": "₹ 5,500",
            "lastPaid": "26-Aug-2025",
            "confirmed": "₹ 5,500",
        },
        {
            "index": 19,
            "worker": {"id": "MN23OP45QR67", "name": "Jack Wright"},
            "indicator": "green-600",
            "lastActive": "7hr ago",
            "accrued": "₹ 4,300",
            "totalPaid": "₹ 4,300",
            "lastPaid": "27-Aug-2025",
            "confirmed": "₹ 4,300",
        },
        {
            "index": 20,
            "worker": {"id": "ST89UV12WX34", "name": "Emily Johnson"},
            "lastActive": "55hr ago",
            "accrued": "₹ 6,000",
            "totalPaid": "₹ 6,000",
            "lastPaid": "28-Aug-2025",
            "confirmed": "₹ 6,000",
        },
    ]

    table = WorkerPaymentsTable(data)
    return render(request, "tailwind/pages/worker_payments.html", {"table": table})


def opportunity_worker(request, org_slug=None, opp_id=None):
    return render(request, "tailwind/pages/opportunity_worker.html", {"header_title": "Workers"})

def invoice_list(request, org_slug=None, opp_id=None):
    return render(request, "tailwind/pages/invoice_list.html", {"header_title": "Invoices"})

def all_invoice_table(request, org_slug=None, opp_id=None):
    data = [
    {
        "index": 1,
        "invoiceNumber": "1AFF2023062678899",
        "amount": "500",
        "dateAdded": "12 Aug, 2025",
        "addedBy": "person@mail.com",
        "status": { "text": "raised", "color": "violet-500", "bgColor": "violet-500/20" },
        "paymentDate": "12 Aug, 2025",
        "serviceDelivery": "Yes",
        "actions": { "list": ["Download Invoice"] }
    },
    {
        "index": 2,
        "invoiceNumber": "1AFF2023062678899",
        "amount": "300",
        "dateAdded": "12 Aug, 2025",
        "addedBy": "person@mail.com",
        "status": { "text": "Approved", "color": "green-600", "bgColor": "green-600/20" },
        "paymentDate": "12 Aug, 2025",
        "serviceDelivery": "Yes",
        "actions": { "list": ["Download Invoice"] }
    },
    {
        "index": 3,
        "invoiceNumber": "1AFF2023062678899",
        "amount": "450",
        "dateAdded": "12 Aug, 2025",
        "addedBy": "person@mail.com",
        "status": { "text": "paid", "color": "violet-500", "bgColor": "violet-500/20" },
        "paymentDate": "12 Aug, 2025",
        "serviceDelivery": "Yes",
        "actions": { "list": ["Download Invoice"] }
    },
    {
        "index": 4,
        "invoiceNumber": "1AFF2023062678899",
        "amount": "550",
        "dateAdded": "12 Aug, 2025",
        "addedBy": "person@mail.com",
        "status": { "text": "Approved", "color": "green-600", "bgColor": "green-600/20" },
        "paymentDate": "12 Aug, 2025",
        "serviceDelivery": "Yes",
        "actions": { "list": ["Download Invoice"] }
    },
    {
        "index": 5,
        "invoiceNumber": "1AFF2023062678899",
        "amount": "100",
        "dateAdded": "12 Aug, 2025",
        "addedBy": "person@mail.com",
        "status": { "text": "rejected", "color": "orange-600", "bgColor": "orange-600/20" },
        "paymentDate": "12 Aug, 2025",
        "serviceDelivery": "Yes",
        "actions": { "list": ["Download Invoice"] }
    },
    {
        "index": 6,
        "invoiceNumber": "1AFF2023062678899",
        "amount": "300",
        "dateAdded": "12 Aug, 2025",
        "addedBy": "person@mail.com",
        "status": { "text": "raised", "color": "violet-500", "bgColor": "violet-500/20" },
        "paymentDate": "12 Aug, 2025",
        "serviceDelivery": "Yes",
        "actions": { "list": ["Download Invoice"] }
    },
    {
        "index": 7,
        "invoiceNumber": "1AFF2023062678899",
        "amount": "700",
        "dateAdded": "12 Aug, 2025",
        "addedBy": "person@mail.com",
        "status": { "text": "Approved", "color": "green-600", "bgColor": "green-600/20" },
        "paymentDate": "12 Aug, 2025",
        "serviceDelivery": "Yes",
        "actions": { "list": ["Download Invoice"] }
    },
    {
        "index": 8,
        "invoiceNumber": "1AFF2023062678899",
        "amount": "250",
        "dateAdded": "12 Aug, 2025",
        "addedBy": "person@mail.com",
        "status": { "text": "rejected", "color": "orange-600", "bgColor": "orange-600/20" },
        "paymentDate": "12 Aug, 2025",
        "serviceDelivery": "Yes",
        "actions": { "list": ["Download Invoice"] }
    },
    {
        "index": 9,
        "invoiceNumber": "1AFF2023062678899",
        "amount": "400",
        "dateAdded": "12 Aug, 2025",
        "addedBy": "person@mail.com",
        "status": { "text": "raised", "color": "violet-500", "bgColor": "violet-500/20" },
        "paymentDate": "12 Aug, 2025",
        "serviceDelivery": "No",
        "actions": { "list": ["Download Invoice"] }
    },
    {
        "index": 10,
        "invoiceNumber": "1AFF2023062678899",
        "amount": "350",
        "dateAdded": "12 Aug, 2025",
        "addedBy": "person@mail.com",
        "status": { "text": "Approved", "color": "green-600", "bgColor": "green-600/20" },
        "paymentDate": "12 Aug, 2025",
        "serviceDelivery": "Yes",
        "actions": { "list": ["Download Invoice"] }
    },
    {
        "index": 11,
        "invoiceNumber": "1AFF2023062678899",
        "amount": "500",
        "dateAdded": "12 Aug, 2025",
        "addedBy": "person@mail.com",
        "status": { "text": "raised", "color": "violet-500", "bgColor": "violet-500/20" },
        "paymentDate": "12 Aug, 2025",
        "serviceDelivery": "Yes",
        "actions": { "list": ["Download Invoice"] }
    },
    {
        "index": 12,
        "invoiceNumber": "1AFF2023062678899",
        "amount": "600",
        "dateAdded": "12 Aug, 2025",
        "addedBy": "person@mail.com",
        "status": { "text": "paid", "color": "violet-500", "bgColor": "violet-500/20" },
        "paymentDate": "12 Aug, 2025",
        "serviceDelivery": "Yes",
        "actions": { "list": ["Download Invoice"] }
    },
    {
        "index": 13,
        "invoiceNumber": "1AFF2023062678899",
        "amount": "200",
        "dateAdded": "12 Aug, 2025",
        "addedBy": "person@mail.com",
        "status": { "text": "Approved", "color": "green-600", "bgColor": "green-600/20" },
        "paymentDate": "12 Aug, 2025",
        "serviceDelivery": "Yes",
        "actions": { "list": ["Download Invoice"] }
    },
    {
        "index": 14,
        "invoiceNumber": "1AFF2023062678899",
        "amount": "500",
        "dateAdded": "12 Aug, 2025",
        "addedBy": "person@mail.com",
        "status": { "text": "raised", "color": "violet-500", "bgColor": "violet-500/20" },
        "paymentDate": "12 Aug, 2025",
        "serviceDelivery": "Yes",
        "actions": { "list": ["Download Invoice"] }
    },
    {
        "index": 15,
        "invoiceNumber": "1AFF2023062678899",
        "amount": "150",
        "dateAdded": "12 Aug, 2025",
        "addedBy": "person@mail.com",
        "status": { "text": "rejected", "color": "orange-600", "bgColor": "orange-600/20" },
        "paymentDate": "12 Aug, 2025",
        "serviceDelivery": "Yes",
        "actions": { "list": ["Download Invoice"] }
    },
    {
        "index": 16,
        "invoiceNumber": "1AFF2023062678899",
        "amount": "450",
        "dateAdded": "12 Aug, 2025",
        "addedBy": "person@mail.com",
        "status": { "text": "paid", "color": "violet-500", "bgColor": "violet-500/20" },
        "paymentDate": "12 Aug, 2025",
        "serviceDelivery": "Yes",
        "actions": { "list": ["Download Invoice"] }
    },
    {
        "index": 17,
        "invoiceNumber": "1AFF2023062678899",
        "amount": "550",
        "dateAdded": "12 Aug, 2025",
        "addedBy": "person@mail.com",
        "status": { "text": "raised", "color": "violet-500", "bgColor": "violet-500/20" },
        "paymentDate": "12 Aug, 2025",
        "serviceDelivery": "Yes",
        "actions": { "list": ["Download Invoice"] }
    },
    {
        "index": 18,
        "invoiceNumber": "1AFF2023062678899",
        "amount": "500",
        "dateAdded": "12 Aug, 2025",
        "addedBy": "person@mail.com",
        "status": { "text": "Approved", "color": "green-600", "bgColor": "green-600/20" },
        "paymentDate": "12 Aug, 2025",
        "serviceDelivery": "Yes",
        "actions": { "list": ["Download Invoice"] }
    },
    {
        "index": 19,
        "invoiceNumber": "1AFF2023062678899",
        "amount": "100",
        "dateAdded": "12 Aug, 2025",
        "addedBy": "person@mail.com",
        "status": { "text": "rejected", "color": "orange-600", "bgColor": "orange-600/20" },
        "paymentDate": "12 Aug, 2025",
        "serviceDelivery": "Yes",
        "actions": { "list": ["Download Invoice"] }
    },
    {
        "index": 20,
        "invoiceNumber": "1AFF2023062678899",
        "amount": "400",
        "dateAdded": "12 Aug, 2025",
        "addedBy": "person@mail.com",
        "status": { "text": "raised", "color": "violet-500", "bgColor": "violet-500/20" },
        "paymentDate": "12 Aug, 2025",
        "serviceDelivery": "Yes",
        "actions": { "list": ["Download Invoice"] }
    }
]

    table = InvoicesListTable(data)
    return render(request, "tailwind/components/tables/index_selectable_table.html",{ "table": table})

def invoice_report_table(request, org_slug=None, opp_id=None):
    data = [
    {
        "index": 1,
        "paymentUnit": "Payment Unit Name",
        "approvedUnit": "10,495",
        "userPaymentAccrued": "$2,350,495",
        "networkManagerPaymentAccrued": "$2,350,495",
    },
    {
        "index": 2,
        "paymentUnit": "Payment Unit Name",
        "approvedUnit": "10,600",
        "userPaymentAccrued": "$2,360,500",
        "networkManagerPaymentAccrued": "$2,360,500",
    },
    {
        "index": 3,
        "paymentUnit": "Payment Unit Name",
        "approvedUnit": "10,750",
        "userPaymentAccrued": "$2,370,750",
        "networkManagerPaymentAccrued": "$2,370,750",
    },
    {
        "index": 4,
        "paymentUnit": "Payment Unit Name",
        "approvedUnit": "10,800",
        "userPaymentAccrued": "$2,380,800",
        "networkManagerPaymentAccrued": "$2,380,800",
    },
    {
        "index": 5,
        "paymentUnit": "Payment Unit Name",
        "approvedUnit": "11,000",
        "userPaymentAccrued": "$2,400,000",
        "networkManagerPaymentAccrued": "$2,400,000",
    },
    {
        "index": 6,
        "paymentUnit": "Payment Unit Name",
        "approvedUnit": "11,100",
        "userPaymentAccrued": "$2,410,100",
        "networkManagerPaymentAccrued": "$2,410,100",
    },
    {
        "index": 7,
        "paymentUnit": "Payment Unit Name",
        "approvedUnit": "11,200",
        "userPaymentAccrued": "$2,420,200",
        "networkManagerPaymentAccrued": "$2,420,200",
    },
    {
        "index": 8,
        "paymentUnit": "Payment Unit Name",
        "approvedUnit": "11,300",
        "userPaymentAccrued": "$2,430,300",
        "networkManagerPaymentAccrued": "$2,430,300",
    },
    {
        "index": 9,
        "paymentUnit": "Payment Unit Name",
        "approvedUnit": "11,500",
        "userPaymentAccrued": "$2,450,500",
        "networkManagerPaymentAccrued": "$2,450,500",
    },
    {
        "index": 10,
        "paymentUnit": "Payment Unit Name",
        "approvedUnit": "11,600",
        "userPaymentAccrued": "$2,460,600",
        "networkManagerPaymentAccrued": "$2,460,600",
    },
    {
        "index": 11,
        "paymentUnit": "Payment Unit Name",
        "approvedUnit": "11,800",
        "userPaymentAccrued": "$2,480,800",
        "networkManagerPaymentAccrued": "$2,480,800",
    },
    {
        "index": 12,
        "paymentUnit": "Payment Unit Name",
        "approvedUnit": "11,900",
        "userPaymentAccrued": "$2,490,900",
        "networkManagerPaymentAccrued": "$2,490,900",
    },
    {
        "index": 13,
        "paymentUnit": "Payment Unit Name",
        "approvedUnit": "12,000",
        "userPaymentAccrued": "$2,500,000",
        "networkManagerPaymentAccrued": "$2,500,000",
    },
    {
        "index": 14,
        "paymentUnit": "Payment Unit Name",
        "approvedUnit": "12,100",
        "userPaymentAccrued": "$2,510,100",
        "networkManagerPaymentAccrued": "$2,510,100",
    },
    {
        "index": 15,
        "paymentUnit": "Payment Unit Name",
        "approvedUnit": "12,300",
        "userPaymentAccrued": "$2,530,300",
        "networkManagerPaymentAccrued": "$2,530,300",
    },
    {
        "index": 16,
        "paymentUnit": "Payment Unit Name",
        "approvedUnit": "12,400",
        "userPaymentAccrued": "$2,540,400",
        "networkManagerPaymentAccrued": "$2,540,400",
    },
    {
        "index": 17,
        "paymentUnit": "Payment Unit Name",
        "approvedUnit": "12,600",
        "userPaymentAccrued": "$2,560,600",
        "networkManagerPaymentAccrued": "$2,560,600",
    },
    {
        "index": 18,
        "paymentUnit": "Payment Unit Name",
        "approvedUnit": "12,700",
        "userPaymentAccrued": "$2,570,700",
        "networkManagerPaymentAccrued": "$2,570,700",
    },
    {
        "index": 19,
        "paymentUnit": "Payment Unit Name",
        "approvedUnit": "12,900",
        "userPaymentAccrued": "$2,590,900",
        "networkManagerPaymentAccrued": "$2,590,900",
    },
    {
        "index": 20,
        "paymentUnit": "Payment Unit Name",
        "approvedUnit": "13,000",
        "userPaymentAccrued": "$2,600,000",
        "networkManagerPaymentAccrued": "$2,600,000",
    }
]

    table = InvoicePaymentReportTable(data)
    return render(request, "tailwind/components/tables/index_selectable_table.html",{ "table": table})

def invoice_report_card(request, org_slug=None, opp_id=None):
    data = [
        {"name": "Worker | <span class='font-medium'>Total</span> Accrued", "icon": "user-friends", "count": "$ 2,250,000"},
        {"name": "Worker | <span class='font-medium'>Total</span> Paid", "icon": "user-friends", "count": "$ 2,250,000"},
        {"name": "Organization | <span class='font-medium'>Total</span> Accrued", "icon": "building", "count": "$ 450,000"},
        {"name": "Organization | <span class='font-medium'>Total</span> Paid", "icon": "building", "count": "$ 100,000"},
    ]
    return render(request, "tailwind/components/cards/invoice_report_card.html", {"data": data})

def get_worker_last_payment(request, org_slug=None, opp_id=None):
    payments = [
        {"date": "12-Jul-2024", "amount": "₹4,780"},
        {"date": "15-Aug-2024", "amount": "₹5,230"},
        {"date": "20-Sep-2024", "amount": "₹4,950"},
        {"date": "25-Oct-2024", "amount": "₹6,100"},
    ]

    html = ""
    for payment in payments:
        html += f"""
            <div class="flex justify-between py-1 my-1 items-center">
                <p class="text-xs text-brand-deep-purple">{payment['date']}</p>
                <p class="text-sm text-slate-900">{payment['amount']}</p>
            </div>
        """

    return HttpResponse(html)


def create_opportunity(request, org_slug=None, opp_id=None):
    step = {
        "selected": "Details",
        "stage": [
            {"index": 1, "label": "Details", "status": True},
            {"index": 2, "label": "Payment Unit", "status": False},
            {"index": 3, "label": "Verification Flags", "status": False},
            {"index": 4, "label": "Budgets", "status": False},
        ],
    }
    return render(request, "tailwind/pages/create_opportunity.html", {"data": step})

def worker_learn(request, org_slug=None, opp_id=None):
    data = [
        {
            "index": 1,
            "worker": {"id": "UV23WX45YZ67", "name": "Isabella Carter"},
            "indicator": "orange-600",
            "lastActive": "9hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
            
        },
        {
            "index": 2,
            "worker": {"id": "OP45QR67ST89", "name": "Alexander Nelson"},
            "indicator": "green-600",
            "lastActive": "9hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 3,
            "worker": {"id": "IJ78KL12MN34", "name": "Evelyn Adams"},
            "indicator": "red-600",
            "lastActive": "95 hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 4,
            "worker": {"id": "GH56IJ78KL90", "name": "Olivia Robinson"},
            "indicator": "yellow-600",
            "lastActive": "12hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 5,
            "worker": {"id": "MN23OP45QR67", "name": "Noah Martinez"},
            "indicator": "gray-600",
            "lastActive": "24hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 6,
            "worker": {"id": "ST89UV12WX34", "name": "Emma Wilson"},
            "lastActive": "48hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 7,
            "worker": {"id": "YZ12AB34CD56", "name": "James Smith"},
            "indicator": "red-600",
            "lastActive": "72hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 8,
            "worker": {"id": "EF78GH90IJ12", "name": "Sophia Johnson"},
            "indicator": "orange-600",
            "lastActive": "24hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 9,
            "worker": {"id": "KL34MN56OP78", "name": "Mason Taylor"},
            "indicator": "green-600",
            "lastActive": "96hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        {
            "index": 10,
            "worker": {"id": "QR12ST34UV56", "name": "Amelia Brown"},
            "indicator": "yellow-600",
            "lastActive": "30hr ago",
            "start_learning": "12-Aug-2025",
            "modules_completed": "50",
            "completed_learning": "12-Aug-2025",
            "assessment":"Passed",
            "attempts":"4",
            "learning_hours":"10h 19m"
        },
        
    ]

    table = WorkerLearnTable(data)
    return render(request, "tailwind/pages/worker_learn.html",{ "table": table})

def worker_delivery(request, org_slug=None, opp_id=None):
    data = [
        {
            "index": 1,
            "worker": {"id": "UV23WX45YZ67", "name": "Isabella Carter"},
            "indicator": "orange-600",
            "lastActive": "9hr ago",
            "payment_units": "Name of the Payment Units",
            "started": "12-Aug-2025",
            "delivered": {
                "count": 2,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over Limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "flagged": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
            "approved": {
                "count": 2,
                "auto": 26,
                "manual": 50,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "rejected": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
        },
        {
            "index": " ",
            "worker": {"id": " ", "name": " "},
            "indicator": " ",
            "lastActive": "9hr ago",
            "payment_units": "Name of the Payment Units",
            "started": "12-Aug-2025",
            "delivered": {
                "count": 2,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over Limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "flagged": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
            "approved": {
                "count": 2,
                "auto": 26,
                "manual": 50,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "rejected": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
        },
        {
            "index": 1,
            "worker": {"id": "UV23WX45YZ67", "name": "Isabella Carter"},
            "indicator": "orange-600",
            "lastActive": "9hr ago",
            "payment_units": "Name of the Payment Units",
            "started": "12-Aug-2025",
            "delivered": {
                "count": 2,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over Limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "flagged": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
            "approved": {
                "count": 2,
                "auto": 26,
                "manual": 50,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "rejected": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
        },
        {
            "index": 1,
            "worker": {"id": "UV23WX45YZ67", "name": "Isabella Carter"},
            "indicator": "orange-600",
            "lastActive": "9hr ago",
            "payment_units": "Name of the Payment Units",
            "started": "12-Aug-2025",
            "delivered": {
                "count": 2,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over Limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "flagged": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
            "approved": {
                "count": 2,
                "auto": 26,
                "manual": 50,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "rejected": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
        },
        {
            "index": 1,
            "worker": {"id": "UV23WX45YZ67", "name": "Isabella Carter"},
            "indicator": "orange-600",
            "lastActive": "9hr ago",
            "payment_units": "Name of the Payment Units",
            "started": "12-Aug-2025",
            "delivered": {
                "count": 2,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over Limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "flagged": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
            "approved": {
                "count": 2,
                "auto": 26,
                "manual": 50,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "rejected": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
        },
        {
            "index": 1,
            "worker": {"id": "UV23WX45YZ67", "name": "Isabella Carter"},
            "indicator": "orange-600",
            "lastActive": "9hr ago",
            "payment_units": "Name of the Payment Units",
            "started": "12-Aug-2025",
            "delivered": {
                "count": 2,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over Limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "flagged": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
            "approved": {
                "count": 2,
                "auto": 26,
                "manual": 50,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "rejected": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
        },
        {
            "index": 1,
            "worker": {"id": "UV23WX45YZ67", "name": "Isabella Carter"},
            "indicator": "orange-600",
            "lastActive": "9hr ago",
            "payment_units": "Name of the Payment Units",
            "started": "12-Aug-2025",
            "delivered": {
                "count": 2,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over Limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "flagged": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
            "approved": {
                "count": 2,
                "auto": 26,
                "manual": 50,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "rejected": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
        },
        {
            "index": 1,
            "worker": {"id": "UV23WX45YZ67", "name": "Isabella Carter"},
            "indicator": "orange-600",
            "lastActive": "9hr ago",
            "payment_units": "Name of the Payment Units",
            "started": "12-Aug-2025",
            "delivered": {
                "count": 2,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over Limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "flagged": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
            "approved": {
                "count": 2,
                "auto": 26,
                "manual": 50,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "rejected": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
        },
        {
            "index": 1,
            "worker": {"id": "UV23WX45YZ67", "name": "Isabella Carter"},
            "indicator": "orange-600",
            "lastActive": "9hr ago",
            "payment_units": "Name of the Payment Units",
            "started": "12-Aug-2025",
            "delivered": {
                "count": 2,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over Limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "flagged": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
            "approved": {
                "count": 2,
                "auto": 26,
                "manual": 50,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "rejected": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
        },
        {
            "index": 1,
            "worker": {"id": "UV23WX45YZ67", "name": "Isabella Carter"},
            "indicator": "orange-600",
            "lastActive": "9hr ago",
            "payment_units": "Name of the Payment Units",
            "started": "12-Aug-2025",
            "delivered": {
                "count": 2,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over Limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "flagged": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
            "approved": {
                "count": 2,
                "auto": 26,
                "manual": 50,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "rejected": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
        },
        {
            "index": 1,
            "worker": {"id": "UV23WX45YZ67", "name": "Isabella Carter"},
            "indicator": "orange-600",
            "lastActive": "9hr ago",
            "payment_units": "Name of the Payment Units",
            "started": "12-Aug-2025",
            "delivered": {
                "count": 2,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over Limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "flagged": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
            "approved": {
                "count": 2,
                "auto": 26,
                "manual": 50,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "rejected": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
        },
        {
            "index": 1,
            "worker": {"id": "UV23WX45YZ67", "name": "Isabella Carter"},
            "indicator": "orange-600",
            "lastActive": "9hr ago",
            "payment_units": "Name of the Payment Units",
            "started": "12-Aug-2025",
            "delivered": {
                "count": 2,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over Limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "flagged": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
            "approved": {
                "count": 2,
                "auto": 26,
                "manual": 50,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "rejected": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
        },
        {
            "index": 1,
            "worker": {"id": "UV23WX45YZ67", "name": "Isabella Carter"},
            "indicator": "orange-600",
            "lastActive": "9hr ago",
            "payment_units": "Name of the Payment Units",
            "started": "12-Aug-2025",
            "delivered": {
                "count": 2,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over Limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "flagged": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
            "approved": {
                "count": 2,
                "auto": 26,
                "manual": 50,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "rejected": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
        },
        {
            "index": 1,
            "worker": {"id": "UV23WX45YZ67", "name": "Isabella Carter"},
            "indicator": "orange-600",
            "lastActive": "9hr ago",
            "payment_units": "Name of the Payment Units",
            "started": "12-Aug-2025",
            "delivered": {
                "count": 2,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over Limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "flagged": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
            "approved": {
                "count": 2,
                "auto": 26,
                "manual": 50,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "rejected": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
        },
        {
            "index": 1,
            "worker": {"id": "UV23WX45YZ67", "name": "Isabella Carter"},
            "indicator": "orange-600",
            "lastActive": "9hr ago",
            "payment_units": "Name of the Payment Units",
            "started": "12-Aug-2025",
            "delivered": {
                "count": 2,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over Limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "flagged": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
            "approved": {
                "count": 2,
                "auto": 26,
                "manual": 50,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "rejected": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
        },
        {
            "index": 1,
            "worker": {"id": "UV23WX45YZ67", "name": "Isabella Carter"},
            "indicator": "orange-600",
            "lastActive": "9hr ago",
            "payment_units": "Name of the Payment Units",
            "started": "12-Aug-2025",
            "delivered": {
                "count": 2,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over Limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "flagged": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
            "approved": {
                "count": 2,
                "auto": 26,
                "manual": 50,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "rejected": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
        },
        {
            "index": 1,
            "worker": {"id": "UV23WX45YZ67", "name": "Isabella Carter"},
            "indicator": "orange-600",
            "lastActive": "9hr ago",
            "payment_units": "Name of the Payment Units",
            "started": "12-Aug-2025",
            "delivered": {
                "count": 2,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over Limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "flagged": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
            "approved": {
                "count": 2,
                "auto": 26,
                "manual": 50,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "rejected": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
        },
        {
            "index": 1,
            "worker": {"id": "UV23WX45YZ67", "name": "Isabella Carter"},
            "indicator": "orange-600",
            "lastActive": "9hr ago",
            "payment_units": "Name of the Payment Units",
            "started": "12-Aug-2025",
            "delivered": {
                "count": 2,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over Limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "flagged": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
            "approved": {
                "count": 2,
                "auto": 26,
                "manual": 50,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "rejected": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
        },
        {
            "index": 1,
            "worker": {"id": "UV23WX45YZ67", "name": "Isabella Carter"},
            "indicator": "orange-600",
            "lastActive": "9hr ago",
            "payment_units": "Name of the Payment Units",
            "started": "12-Aug-2025",
            "delivered": {
                "count": 2,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over Limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "flagged": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
            "approved": {
                "count": 2,
                "auto": 26,
                "manual": 50,
                "options": [
                    {"name": "Completed", "value": 25},
                    {"name": "Duplicate", "value": 50},
                    {"name": "Over limit", "value": 75},
                    {"name": "Incomplete", "value": 100},
                ],
            },
            "rejected": {
                "count": 2,
                "options": [
                    {"name": "Location", "value": 25},
                    {"name": "Form Duration", "value": 50},
                    {"name": "Photos", "value": 75},
                    {"name": "Flag Name 1", "value": 100},
                ],
            },
        }
    ]
    
    
    table = WorkerDeliveryTable(data)
    return render(request, "tailwind/pages/worker_delivery.html", {"table": table})
    
    
def pay_worker(request, org_slug=None, opp_id=None):

    data = [
    {
        "index": 1,
        "worker": "Worker 1",
        "unpaid": "$ 4,780",
        "toBePaid": "4,780",
        "paymentDate": "2025-08-12"
    },
    {
        "index": 2,
        "worker": "Worker 2",
        "unpaid": "$ 3,500",
        "toBePaid": "3,500",
        "paymentDate": "15-Aug-2025"
    },
    {
        "index": 3,
        "worker": "Worker 3",
        "unpaid": "$ 2,950",
        "toBePaid": "2,950",
        "paymentDate": "18-Aug-2025"
    },
    {
        "index": 4,
        "worker": "Worker 4",
        "unpaid": "$ 5,600",
        "toBePaid": "5,600",
        "paymentDate": "20-Aug-2025"
    },
    {
        "index": 5,
        "worker": "Worker 5",
        "unpaid": "$ 6,120",
        "toBePaid": "6,120",
        "paymentDate": "22-Aug-2025"
    },
    {
        "index": 6,
        "worker": "Worker 6",
        "unpaid": "$ 4,300",
        "toBePaid": "4,300",
        "paymentDate": "25-Aug-2025"
    },
    {
        "index": 7,
        "worker": "Worker 7",
        "unpaid": "$ 3,950",
        "toBePaid": "3,950",
        "paymentDate": "28-Aug-2025"
    },
    {
        "index": 8,
        "worker": "Worker 8",
        "unpaid": "$ 2,800",
        "toBePaid": "2,800",
        "paymentDate": "30-Aug-2025"
    },
    {
        "index": 9,
        "worker": "Worker 9",
        "unpaid": "$ 3,600",
        "toBePaid": "3,600",
        "paymentDate": "02-Sep-2025"
    },
    {
        "index": 10,
        "worker": "Worker 10",
        "unpaid": "$ 4,200",
        "toBePaid": "4,200",
        "paymentDate": "05-Sep-2025"
    },
    {
        "index": 11,
        "worker": "Worker 11",
        "unpaid": "$ 7,000",
        "toBePaid": "7,000",
        "paymentDate": "08-Sep-2025"
    },
    {
        "index": 12,
        "worker": "Worker 12",
        "unpaid": "$ 5,500",
        "toBePaid": "5,500",
        "paymentDate": "10-Sep-2025"
    },
    {
        "index": 13,
        "worker": "Worker 13",
        "unpaid": "$ 6,900",
        "toBePaid": "6,900",
        "paymentDate": "12-Sep-2025"
    },
    {
        "index": 14,
        "worker": "Worker 14",
        "unpaid": "$ 8,100",
        "toBePaid": "8,100",
        "paymentDate": "15-Sep-2025"
    },
    {
        "index": 15,
        "worker": "Worker 15",
        "unpaid": "$ 3,300",
        "toBePaid": "3,300",
        "paymentDate": "18-Sep-2025"
    },
    {
        "index": 16,
        "worker": "Worker 16",
        "unpaid": "$ 4,500",
        "toBePaid": "4,500",
        "paymentDate": "20-Sep-2025"
    },
    {
        "index": 17,
        "worker": "Worker 17",
        "unpaid": "$ 5,300",
        "toBePaid": "5,300",
        "paymentDate": "22-Sep-2025"
    },
    {
        "index": 18,
        "worker": "Worker 18",
        "unpaid": "$ 6,000",
        "toBePaid": "6,000",
        "paymentDate": "25-Sep-2025"
    },
    {
        "index": 19,
        "worker": "Worker 19",
        "unpaid": "$ 7,800",
        "toBePaid": "7,800",
        "paymentDate": "28-Sep-2025"
    },
    {
        "index": 20,
        "worker": "Worker 20",
        "unpaid": "$ 9,000",
        "toBePaid": "9,000",
        "paymentDate": "30-Sep-2025"
    }
]

    table = PayWorker(data)
    
    return render(request, "tailwind/components/tables/table.html",{ "table": table})

def worker_main(request, org_slug=None, opp_id=None):
    data = [
        {"index": 1, "worker": {"id": "UV23WX45YZ67", "name": "Isabella Carter"}, "indicator": "green-600", "lastActive": "22-Aug-2025", "inviteDate": "22-Aug-2025", "startedLearn": "22-Aug-2025", "completedLearn": "22-Aug-2025", "daysToCompleteLearn": "3 days", "firstDeliveryDate": "25-Aug-2025", "daysToStartDelivery": "3 days"},
        {"index": 2, "worker": {"id": "AB34YZ56LM90", "name": "John Doe"}, "indicator": "blue-500", "lastActive": "23-Aug-2025", "inviteDate": "23-Aug-2025", "startedLearn": "23-Aug-2025", "completedLearn": "23-Aug-2025", "daysToCompleteLearn": "3 days", "firstDeliveryDate": "26-Aug-2025", "daysToStartDelivery": "3 days"},
        {"index": 3, "worker": {"id": "BC45KL67OP89", "name": "Emma Smith"}, "indicator": "red-700", "lastActive": "24-Aug-2025", "inviteDate": "24-Aug-2025", "startedLearn": "24-Aug-2025", "completedLearn": "24-Aug-2025", "daysToCompleteLearn": "3 days", "firstDeliveryDate": "27-Aug-2025", "daysToStartDelivery": "3 days"},
        {"index": 4, "worker": {"id": "CD56MN78QR12", "name": "Michael Johnson"}, "indicator": "yellow-300", "lastActive": "25-Aug-2025", "inviteDate": "25-Aug-2025", "startedLearn": "25-Aug-2025", "completedLearn": "25-Aug-2025", "daysToCompleteLearn": "3 days", "firstDeliveryDate": "28-Aug-2025", "daysToStartDelivery": "3 days"},
        {"index": 5, "worker": {"id": "EF67OP89RS23", "name": "Sophia Brown"}, "indicator": "orange-500", "lastActive": "26-Aug-2025", "inviteDate": "26-Aug-2025", "startedLearn": "26-Aug-2025", "completedLearn": "26-Aug-2025", "daysToCompleteLearn": "3 days", "firstDeliveryDate": "29-Aug-2025", "daysToStartDelivery": "3 days"},
        {"index": 6, "worker": {"id": "GH78QR90ST34", "name": "Daniel Lee"}, "indicator": "green-700", "lastActive": "27-Aug-2025", "inviteDate": "27-Aug-2025", "startedLearn": "27-Aug-2025", "completedLearn": "27-Aug-2025", "daysToCompleteLearn": "3 days", "firstDeliveryDate": "30-Aug-2025", "daysToStartDelivery": "3 days"},
        {"index": 7, "worker": {"id": "IJ89ST01UV45", "name": "Olivia Harris"}, "indicator": "purple-400", "lastActive": "28-Aug-2025", "inviteDate": "28-Aug-2025", "startedLearn": "28-Aug-2025", "completedLearn": "28-Aug-2025", "daysToCompleteLearn": "3 days", "firstDeliveryDate": "31-Aug-2025", "daysToStartDelivery": "3 days"},
        {"index": 8, "worker": {"id": "KL90UV12WX56", "name": "James Wilson"}, "indicator": "blue-400", "lastActive": "29-Aug-2025", "inviteDate": "29-Aug-2025", "startedLearn": "29-Aug-2025", "completedLearn": "29-Aug-2025", "daysToCompleteLearn": "3 days", "firstDeliveryDate": "01-Sep-2025", "daysToStartDelivery": "3 days"},
        {"index": 9, "worker": {"id": "MN01VW23XY67", "name": "Charlotte Scott"}, "indicator": "pink-500", "lastActive": "30-Aug-2025", "inviteDate": "30-Aug-2025", "startedLearn": "30-Aug-2025", "completedLearn": "30-Aug-2025", "daysToCompleteLearn": "3 days", "firstDeliveryDate": "02-Sep-2025", "daysToStartDelivery": "3 days"},
        {"index": 10, "worker": {"id": "OP12XY34ZA89", "name": "William Moore"}, "indicator": "cyan-600", "lastActive": "31-Aug-2025", "inviteDate": "31-Aug-2025", "startedLearn": "31-Aug-2025", "completedLearn": "31-Aug-2025", "daysToCompleteLearn": "3 days", "firstDeliveryDate": "03-Sep-2025", "daysToStartDelivery": "3 days"},
        {"index": 11, "worker": {"id": "QR23YZ45AB01", "name": "Ava Clark"}, "indicator": "brown-700", "lastActive": "01-Sep-2025", "inviteDate": "01-Sep-2025", "startedLearn": "01-Sep-2025", "completedLearn": "01-Sep-2025", "daysToCompleteLearn": "3 days", "firstDeliveryDate": "04-Sep-2025", "daysToStartDelivery": "3 days"},
        {"index": 12, "worker": {"id": "ST34AB56CD12", "name": "Lucas Lewis"}, "indicator": "teal-500", "lastActive": "02-Sep-2025", "inviteDate": "02-Sep-2025", "startedLearn": "02-Sep-2025", "completedLearn": "02-Sep-2025", "daysToCompleteLearn": "3 days", "firstDeliveryDate": "05-Sep-2025", "daysToStartDelivery": "3 days"},
        {"index": 13, "worker": {"id": "UV45BC67EF23", "name": "Amelia Walker"}, "indicator": "grey-400", "lastActive": "03-Sep-2025", "inviteDate": "03-Sep-2025", "startedLearn": "03-Sep-2025", "completedLearn": "03-Sep-2025", "daysToCompleteLearn": "3 days", "firstDeliveryDate": "06-Sep-2025", "daysToStartDelivery": "3 days"},
        {"index": 14, "worker": {"id": "WX56DE78FG34", "name": "Mason Allen"}, "indicator": "lime-500", "lastActive": "04-Sep-2025", "inviteDate": "04-Sep-2025", "startedLearn": "04-Sep-2025", "completedLearn": "04-Sep-2025", "daysToCompleteLearn": "3 days", "firstDeliveryDate": "07-Sep-2025", "daysToStartDelivery": "3 days"},
        {"index": 15, "worker": {"id": "YZ67FG89HI45", "name": "Ethan Harris"}, "indicator": "indigo-600", "lastActive": "05-Sep-2025", "inviteDate": "05-Sep-2025", "startedLearn": "05-Sep-2025", "completedLearn": "05-Sep-2025", "daysToCompleteLearn": "3 days", "firstDeliveryDate": "08-Sep-2025", "daysToStartDelivery": "3 days"}
    ]



    table = WorkerMainTable(data)
    return render(request, "tailwind/pages/worker_main.html",{ "table": table})


def payment_history(request, org_slug=None, opp_id=None):

    data = [
  {
    "date": "01-Mar-2024",
    "time": "08:30 AM",
    "title": "By Network Manager",
    "status": "Paid",
    "amount": "$3,500",
    "workers": 50
  },
  {
    "date": "02-Mar-2024",
    "time": "09:15 AM",
    "title": "By Network Manager",
    "status": "Failed",
    "amount": "$1,200",
    "workers": 30
  },
  {
    "date": "03-Mar-2024",
    "time": "10:00 AM",
    "title": "By Network Manager",
    "status": "Paid",
    "amount": "$4,000",
    "workers": 60
  },
  {
    "date": "04-Mar-2024",
    "time": "11:45 AM",
    "title": "By Network Manager",
    "status": "Paid",
    "amount": "$2,800",
    "workers": 40
  },
  {
    "date": "05-Mar-2024",
    "time": "12:30 PM",
    "title": "By Network Manager",
    "status": "Failed",
    "amount": "$1,000",
    "workers": 20
  },
  {
    "date": "06-Mar-2024",
    "time": "01:00 PM",
    "title": "By Network Manager",
    "status": "Paid",
    "amount": "$5,000",
    "workers": 75
  },
  {
    "date": "07-Mar-2024",
    "time": "02:45 PM",
    "title": "By Network Manager",
    "status": "Failed",
    "amount": "$900",
    "workers": 15
  },
  {
    "date": "08-Mar-2024",
    "time": "03:30 PM",
    "title": "By Network Manager",
    "status": "Paid",
    "amount": "$6,200",
    "workers": 90
  },
  {
    "date": "09-Mar-2024",
    "time": "04:15 PM",
    "title": "By Network Manager",
    "status": "Paid",
    "amount": "$3,800",
    "workers": 55
  },
  {
    "date": "10-Mar-2024",
    "time": "05:00 PM",
    "title": "By Network Manager",
    "status": "Failed",
    "amount": "$1,500",
    "workers": 25
  },
  {
    "date": "08-Mar-2024",
    "time": "03:30 PM",
    "title": "By Network Manager",
    "status": "Paid",
    "amount": "$6,200",
    "workers": 90
  },
  {
    "date": "09-Mar-2024",
    "time": "04:15 PM",
    "title": "By Network Manager",
    "status": "Paid",
    "amount": "$3,800",
    "workers": 55
  },
  {
    "date": "10-Mar-2024",
    "time": "05:00 PM",
    "title": "By Network Manager",
    "status": "Failed",
    "amount": "$1,500",
    "workers": 25
  },
  {
    "date": "08-Mar-2024",
    "time": "03:30 PM",
    "title": "By Network Manager",
    "status": "Paid",
    "amount": "$6,200",
    "workers": 90
  },
  {
    "date": "09-Mar-2024",
    "time": "04:15 PM",
    "title": "By Network Manager",
    "status": "Paid",
    "amount": "$3,800",
    "workers": 55
  },
  {
    "date": "10-Mar-2024",
    "time": "05:00 PM",
    "title": "By Network Manager",
    "status": "Failed",
    "amount": "$1,500",
    "workers": 25
  }
]

    return render(request, "opportunity/tailwind/components/payment_history.html", {"data": data})

def worker_flagged_table(request,org_slug=None,opp_id=None):
    data = [
        {"index": i+1, "time": "14:56", "entity_name": "Viollo Maeya", "flags": ['Location','Form Duration','Photos'], "reportIcons": ['flag']}
        for i in range(25)
    ]
    table = FlaggedWorkerTable(data)    
    return render(request, "tailwind/components/worker_page/table.html", {"table": table})

def worker_review_table(request,org_slug=None,opp_id=None):
    data = [
        {"index": i+1, "time": "14:56", "entity_name": "Viollo Maeya", "flags": ['Location','Form Duration','Photos'], 
         "last_activity": "12 Aug 2025", "reportIcons": ['pending','partial']}
        for i in range(25)
    ]
    table = CommonWorkerTable(data)    
    return render(request, "tailwind/components/worker_page/table.html", {"table": table})

def worker_revalidate_table(request,org_slug=None,opp_id=None):
    data = [
        {"index": i+1, "time": "14:56", "entity_name": "Viollo Maeya", "flags": ['Location','Form Duration','Photos'], 
         "last_activity": "12 Aug 2025", "reportIcons": ['reject','partial']}
        for i in range(25)
    ]
    table = CommonWorkerTable(data)    
    return render(request, "tailwind/components/worker_page/table.html", {"table": table})

def worker_approved_table(request,org_slug=None,opp_id=None):
    data = [
        {"index": i+1, "time": "14:56", "entity_name": "Viollo Maeya", "flags": ['Location','Form Duration','Photos'], 
         "last_activity": "12 Aug 2025", "reportIcons": ['accept','approved']}
        for i in range(25)
    ]
    table = CommonWorkerTable(data)    
    return render(request, "tailwind/components/worker_page/table.html", {"table": table})

def worker_rejected_table(request,org_slug=None,opp_id=None):
    data = [
        {"index": i+1, "time": "14:56", "entity_name": "Viollo Maeya", "flags": ['Location','Form Duration','Photos'], 
         "last_activity": "12 Aug 2025", "reportIcons": ['reject','cancelled']}
        for i in range(25)
    ]
    table = CommonWorkerTable(data)    
    return render(request, "tailwind/components/worker_page/table.html", {"table": table})

def worker_all_table(request,org_slug=None,opp_id=None):
    # Possible icon combinations for the all table
    icon_options = [
        ['cancelled','partial'],
        ['pending','reject'],
        ['accept','approved'],
        ['reject','partial'],
        ['pending','partial'],
        ['flag'],
        ['approved'],
        ['cancelled']
    ]
    
    data = [
        {
            "index": i+1,
            "date": "12 Jul, 2024",
            "time": "14:56",
            "entity_name": "Viollo Maeya",
            "flags": ['Location','Form Duration','Photos'],
            "last_activity": "12 Aug 2025",
            "reportIcons": icon_options[i % len(icon_options)]  # Cycle through icon options
        }
        for i in range(25)
    ]
    table = AllWorkerTable(data)    
    return render(request, "tailwind/components/worker_page/table.html", {"table": table})
def my_organization(request, org_slug=None, opp_id=None):
     return render(request, "tailwind/pages/my_organization.html", {"header_title": "My Organization"})

def my_organization_members_table(request, org_slug=None, opp_id=None):
    data = [
        {"index": 1, "member": "John Doe", "status": "active", "email": "5A0oR@example.com", "addedOn": "12-Jul-2025", "addedBy": "John Doe", "role": "Admin"},
        {"index": 2, "member": "Jane Smith", "status": "inactive", "email": "jane.smith@example.com", "addedOn": "15-Jul-2025", "addedBy": "John Doe", "role": "User"},
        {"index": 3, "member": "Alice Brown", "status": "active", "email": "alice.brown@example.com", "addedOn": "20-Jul-2025", "addedBy": "John Doe", "role": "Manager"},
        {"index": 4, "member": "Bob Johnson", "status": "active", "email": "bob.johnson@example.com", "addedOn": "21-Jul-2025", "addedBy": "Jane Smith", "role": "User"},
        {"index": 5, "member": "Charlie Davis", "status": "inactive", "email": "charlie.davis@example.com", "addedOn": "22-Jul-2025", "addedBy": "Alice Brown", "role": "Admin"},
        {"index": 6, "member": "David Lee", "status": "active", "email": "david.lee@example.com", "addedOn": "23-Jul-2025", "addedBy": "Bob Johnson", "role": "Manager"},
        {"index": 7, "member": "Eva Green", "status": "inactive", "email": "eva.green@example.com", "addedOn": "25-Jul-2025", "addedBy": "David Lee", "role": "User"},
        {"index": 8, "member": "Frank White", "status": "active", "email": "frank.white@example.com", "addedOn": "28-Jul-2025", "addedBy": "Eva Green", "role": "Admin"},
        {"index": 9, "member": "Grace King", "status": "active", "email": "grace.king@example.com", "addedOn": "30-Jul-2025", "addedBy": "Frank White", "role": "Manager"},
        {"index": 10, "member": "Hannah Scott", "status": "inactive", "email": "hannah.scott@example.com", "addedOn": "01-Aug-2025", "addedBy": "Grace King", "role": "User"},
        {"index": 11, "member": "Ian Harris", "status": "active", "email": "ian.harris@example.com", "addedOn": "05-Aug-2025", "addedBy": "Hannah Scott", "role": "Admin"},
        {"index": 12, "member": "Jack Thomas", "status": "inactive", "email": "jack.thomas@example.com", "addedOn": "07-Aug-2025", "addedBy": "Ian Harris", "role": "User"},
        {"index": 13, "member": "Katherine Adams", "status": "active", "email": "katherine.adams@example.com", "addedOn": "10-Aug-2025", "addedBy": "Jack Thomas", "role": "Manager"},
        {"index": 14, "member": "Liam Carter", "status": "inactive", "email": "liam.carter@example.com", "addedOn": "12-Aug-2025", "addedBy": "Katherine Adams", "role": "Admin"},
        {"index": 15, "member": "Monica Clark", "status": "active", "email": "monica.clark@example.com", "addedOn": "15-Aug-2025", "addedBy": "Liam Carter", "role": "User"},
        {"index": 16, "member": "Nathaniel Walker", "status": "active", "email": "nathaniel.walker@example.com", "addedOn": "17-Aug-2025", "addedBy": "Monica Clark", "role": "Manager"},
        {"index": 17, "member": "Olivia Hall", "status": "inactive", "email": "olivia.hall@example.com", "addedOn": "20-Aug-2025", "addedBy": "Nathaniel Walker", "role": "User"},
        {"index": 18, "member": "Paul Allen", "status": "active", "email": "paul.allen@example.com", "addedOn": "22-Aug-2025", "addedBy": "Olivia Hall", "role": "Admin"},
        {"index": 19, "member": "Quincy Adams", "status": "active", "email": "quincy.adams@example.com", "addedOn": "25-Aug-2025", "addedBy": "Paul Allen", "role": "Manager"},
        {"index": 20, "member": "Rachel Young", "status": "inactive", "email": "rachel.young@example.com", "addedOn": "28-Aug-2025", "addedBy": "Quincy Adams", "role": "User"}
    ]
    
    table = MyOrganizationMembersTable(data)
    return render(request, "tailwind/components/tables/index_selectable_table.html",{ "table": table})

def opportunity_worker_learn_progress(request, org_slug=None, opp_id=None):
    user_kpi = [
           {"name":"<span class='font-medium'>Total Time</span> Learning", "icon":"book-open-cover","count":"19hr 12min" },
    ]
    data = [
        {
            "index": 1,
            "moduleName": "Module 1",
            "dateCompleted": "12-Aug-2025",
            "timeCompleted": "14:56",
            "duration": "19hr 12min",
        },
        {
            "index": 2,
            "moduleName": "Module 2",
            "dateCompleted": "12-Aug-2025",
            "timeCompleted": "14:56",
            "duration": "19hr 12min",
        },
        {
            "index": 3,
            "moduleName": "Module 3",
            "dateCompleted": "12-Aug-2025",
            "timeCompleted": "14:56",
            "duration": "19hr 12min",
        },
        {
            "index": 4,
            "moduleName": "Module 4",
            "dateCompleted": "12-Aug-2025",
            "timeCompleted": "14:56",
            "duration": "19hr 12min",
        },
    ]
    table = OpportunityWorkerLearnProgressTable(data)
    return render(request, "tailwind/pages/opportunity_worker_extended.html", {"header_title": "Worker", "kpi":user_kpi, "tab_name": "Learn Progress", "table": table })    


def opportunity_worker_payment(request, org_slug=None, opp_id=None):
    user_kpi = [
           {"name":"<span class='font-medium'>Accrued</span> Payment", "icon":"money-bill-wave","count":"$4,780" },
           {"name":"<span class='font-medium'>Due</span> Payment", "icon":"timer","count":"$1,780", "dropdown":"True" },
           {"name":"<span class='font-medium'>Paid</span> Payment", "icon":"hand-holding-dollar","count":"$3,000", "dropdown":"True" },
    ]
    data = [
        {
            "index": 1,
            "amountPaid": "$4,800",
            "dateCompleted": "12-Aug-2025",
            "timeCompleted": "14:56",
        },
        {
            "index": 2,
            "amountPaid": "$4,800",
            "dateCompleted": "12-Aug-2025",
            "timeCompleted": "14:56",
        },
        {
            "index": 3,
            "amountPaid": "$4,800",
            "dateCompleted": "12-Aug-2025",
            "timeCompleted": "14:56",
        },
        {
            "index": 4,
            "amountPaid": "$4,800",
            "dateCompleted": "12-Aug-2025",
            "timeCompleted": "14:56",
        },
    ]
    table = OpportunityWorkerPaymentTable(data)
    return render(request, "tailwind/pages/opportunity_worker_extended.html", {"header_title": "Worker", "kpi":user_kpi, "tab_name": "Payment", "table": table })    
