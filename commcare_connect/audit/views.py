import json
import threading

from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import DetailView, TemplateView, View
from django_tables2 import SingleTableView

from commcare_connect.audit.management.extractors.connect_api_facade import ConnectAPIFacade
from commcare_connect.audit.models import Assessment, Audit, AuditResult
from commcare_connect.audit.services.audit_creator import create_audit_sessions, preview_audit_sessions
from commcare_connect.audit.services.database_manager import get_database_stats, reset_audit_database
from commcare_connect.audit.services.progress_tracker import ProgressTracker
from commcare_connect.audit.tables import AuditTable
from commcare_connect.opportunity.models import BlobMeta, UserVisit

User = get_user_model()


class AuditListView(LoginRequiredMixin, SingleTableView):
    """List all audit sessions"""

    model = Audit
    table_class = AuditTable
    template_name = "audit/audit_session_list.html"
    paginate_by = 20

    def get_queryset(self):
        return Audit.objects.order_by("-created_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Check for Connect OAuth token
        from django.conf import settings

        # In labs mode, OAuth token is in session
        if getattr(settings, "IS_LABS_ENVIRONMENT", False):
            labs_oauth = self.request.session.get("labs_oauth", {})
            context["has_connect_token"] = bool(labs_oauth.get("access_token"))
            if labs_oauth.get("expires_at"):
                import datetime

                from django.utils import timezone

                context["token_expires_at"] = datetime.datetime.fromtimestamp(
                    labs_oauth["expires_at"], tz=timezone.get_current_timezone()
                )
            else:
                context["token_expires_at"] = None
        else:
            # Normal mode: check database for SocialAccount
            from allauth.socialaccount.models import SocialAccount, SocialToken

            try:
                social_account = SocialAccount.objects.get(user=self.request.user, provider="connect")
                social_token = SocialToken.objects.get(account=social_account)
                context["has_connect_token"] = True
                context["token_expires_at"] = social_token.expires_at
            except (SocialAccount.DoesNotExist, SocialToken.DoesNotExist):
                context["has_connect_token"] = False
                context["token_expires_at"] = None

        return context


class AuditDetailView(LoginRequiredMixin, DetailView):
    """Main audit interface for reviewing visits one at a time"""

    model = Audit
    template_name = "audit/audit_session_detail.html"
    context_object_name = "session"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        session = self.get_object()

        # Get all UserVisits explicitly assigned to this session
        # Note: deliver_unit can be NULL, so we don't include it in select_related (INNER JOIN)
        all_visits = session.visits.all().select_related("user", "opportunity").order_by("visit_date")

        # Get existing audit results
        existing_results = {result.user_visit_id: result for result in session.results.select_related("user_visit")}

        # Get current visit index from URL parameter (1-based, convert to 0-based for array access)
        visit_index = int(self.request.GET.get("visit", 1)) - 1
        total_visits = all_visits.count()

        # Ensure visit_index is within bounds
        if visit_index < 0:
            visit_index = 0
        elif visit_index >= total_visits:
            visit_index = max(0, total_visits - 1)

        # Get current visit
        current_visit = None
        if total_visits > 0:
            current_visit = all_visits[visit_index]
            current_visit.audit_result = existing_results.get(current_visit.id)

            # Attach assessments to images for easy template access
            current_visit_images = []
            if current_visit.audit_result:
                assessments = Assessment.objects.filter(audit_result=current_visit.audit_result)
                assessments_by_blob = {a.blob_id: a for a in assessments}

                # Fetch images and attach assessments
                for image in current_visit.images.all():
                    image.assessment = assessments_by_blob.get(image.blob_id)
                    current_visit_images.append(image)

        # Progress information (based on assessed images, not visits)
        from commcare_connect.audit.helpers import calculate_audit_progress

        progress_percentage, assessed_count, total_assessments = calculate_audit_progress(session)
        pending_count = total_assessments - assessed_count

        context.update(
            {
                "current_visit": current_visit,
                "current_visit_images": current_visit_images,
                "visit_index": visit_index,
                "total_visits": total_visits,
                "audited_count": assessed_count,
                "pending_count": pending_count,
                "progress_percentage": progress_percentage,
                "has_previous": visit_index > 0,
                "has_next": visit_index < total_visits - 1,
                "previous_index": visit_index - 1 if visit_index > 0 else 0,
                "next_index": visit_index + 1 if visit_index < total_visits - 1 else visit_index,
            }
        )

        return context


class AuditResultUpdateView(LoginRequiredMixin, View):
    """AJAX endpoint for updating audit results"""

    def post(self, request, session_id):
        try:
            session = get_object_or_404(Audit, id=session_id)
            visit_id = request.POST.get("visit_id")
            result = request.POST.get("result")
            notes = request.POST.get("notes", "")
            auto_advance = request.POST.get("auto_advance", "true") == "true"
            current_index = int(request.POST.get("current_index", 0))

            if not visit_id or result not in ["pass", "fail"]:
                return JsonResponse({"error": "Invalid data"}, status=400)

            visit = get_object_or_404(UserVisit, id=visit_id)

            # Create or update audit result
            audit_result, created = AuditResult.objects.update_or_create(
                audit_session=session, user_visit=visit, defaults={"result": result, "notes": notes}
            )

            # TODO: Update to use Assessment model instead of image_notes
            # For now, we'll just save the result without individual image assessments
            # Image notes handling will be reimplemented with Assessment model

            # Calculate updated progress using explicit visit set
            total_visits = session.visits.count()

            audited_count = session.results.count()
            progress_percentage = round((audited_count / total_visits) * 100, 1) if total_visits > 0 else 0

            # Determine next visit index for auto-advance
            next_index = current_index + 1 if auto_advance and current_index + 1 < total_visits else current_index

            return JsonResponse(
                {
                    "success": True,
                    "created": created,
                    "progress_percentage": progress_percentage,
                    "audited_count": audited_count,
                    "total_visits": total_visits,
                    "next_index": next_index,
                    "should_advance": auto_advance and next_index != current_index,
                }
            )

        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


class AuditCompleteView(LoginRequiredMixin, View):
    """Complete an audit session"""

    def post(self, request, session_id):
        try:
            session = get_object_or_404(Audit, id=session_id)

            overall_result = request.POST.get("overall_result")
            notes = request.POST.get("notes", "")
            kpi_notes = request.POST.get("kpi_notes", "")

            if overall_result not in ["pass", "fail"]:
                return JsonResponse({"error": "Invalid overall result"}, status=400)

            session.status = Audit.Status.COMPLETED
            session.overall_result = overall_result
            session.notes = notes
            session.kpi_notes = kpi_notes
            session.completed_at = timezone.now()
            session.save()

            return JsonResponse({"success": True})

        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


class AuditUncompleteView(LoginRequiredMixin, View):
    """Reopen a completed audit session"""

    def post(self, request, session_id):
        try:
            session = get_object_or_404(Audit, id=session_id)

            if session.status != Audit.Status.COMPLETED:
                return JsonResponse({"error": "Audit session is not completed"}, status=400)

            session.status = Audit.Status.IN_PROGRESS
            session.completed_at = None
            session.save()

            return JsonResponse({"success": True})

        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


class AuditExportView(LoginRequiredMixin, DetailView):
    """Export audit session results as JSON"""

    model = Audit

    def get(self, request, *args, **kwargs):
        session = self.get_object()

        # Generate export data
        export_data = {
            "audit_session": {
                "id": session.id,
                "auditor_username": session.auditor_username,
                "flw_username": session.flw_username,
                "opportunity_name": session.opportunity_name,
                "domain": session.domain,
                "app_id": session.app_id,
                "date_range": [str(session.start_date), str(session.end_date)],
                "overall_result": session.overall_result,
                "notes": session.notes,
                "kpi_notes": session.kpi_notes,
                "created_at": session.created_at.isoformat(),
                "completed_at": session.completed_at.isoformat() if session.completed_at else None,
            },
            "visit_results": [],
        }

        # Build visit results with images and assessments
        for result in session.results.select_related("user_visit__user").prefetch_related("assessments").all():
            # Get assessments by blob_id for easy lookup
            assessments_by_blob = {a.blob_id: a for a in result.assessments.all()}

            # Build images list with assessment data
            images = []
            for img in result.user_visit.images.all():
                assessment = assessments_by_blob.get(img.blob_id)
                image_data = {
                    "filename": img.name,
                    "blob_id": img.blob_id,
                    "question_id": img.question_id,
                }
                # Include assessment data if it exists
                if assessment:
                    image_data["assessment"] = {
                        "result": assessment.result,
                        "notes": assessment.notes,
                        "assessed_at": assessment.assessed_at.isoformat() if assessment.assessed_at else None,
                        "assessment_type": assessment.assessment_type,
                    }
                images.append(image_data)

            visit_result = {
                "visit_id": result.user_visit.id,
                "xform_id": result.user_visit.xform_id,
                "visit_date": result.user_visit.visit_date.isoformat(),
                "entity_id": result.user_visit.entity_id,
                "entity_name": result.user_visit.entity_name,
                "user_username": result.user_visit.user.username,
                "result": result.result,
                "notes": result.notes,
                "images": images,
                "image_count": len(images),
                "audited_at": result.audited_at.isoformat(),
            }
            export_data["visit_results"].append(visit_result)

        # Return as JSON download
        response = HttpResponse(
            content_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="audit_export_{session.id}.json"'},
        )

        json.dump(export_data, response, indent=2)
        return response


class AuditExportAllView(LoginRequiredMixin, View):
    """Export all completed audit sessions as a single JSON file"""

    def get(self, request):
        # Get all completed audit sessions
        completed_sessions = Audit.objects.filter(status=Audit.Status.COMPLETED).order_by("-completed_at")

        if not completed_sessions.exists():
            return JsonResponse({"error": "No completed audit sessions to export"}, status=404)

        # Generate export data for all sessions
        all_audits = []
        for session in completed_sessions:
            visit_results = []

            # Build visit results with images and assessments
            for result in session.results.select_related("user_visit__user").prefetch_related("assessments").all():
                # Get assessments by blob_id for easy lookup
                assessments_by_blob = {a.blob_id: a for a in result.assessments.all()}

                # Build images list with assessment data
                images = []
                for img in result.user_visit.images.all():
                    assessment = assessments_by_blob.get(img.blob_id)
                    image_data = {
                        "filename": img.name,
                        "blob_id": img.blob_id,
                        "question_id": img.question_id,
                    }
                    # Include assessment data if it exists
                    if assessment:
                        image_data["assessment"] = {
                            "result": assessment.result,
                            "notes": assessment.notes,
                            "assessed_at": assessment.assessed_at.isoformat() if assessment.assessed_at else None,
                            "assessment_type": assessment.assessment_type,
                        }
                    images.append(image_data)

                visit_result = {
                    "visit_id": result.user_visit.id,
                    "xform_id": result.user_visit.xform_id,
                    "visit_date": result.user_visit.visit_date.isoformat(),
                    "entity_id": result.user_visit.entity_id,
                    "entity_name": result.user_visit.entity_name,
                    "user_username": result.user_visit.user.username,
                    "result": result.result,
                    "notes": result.notes,
                    "images": images,
                    "image_count": len(images),
                    "audited_at": result.audited_at.isoformat(),
                }
                visit_results.append(visit_result)

            audit_data = {
                "audit_session": {
                    "id": session.id,
                    "auditor_username": session.auditor_username,
                    "flw_username": session.flw_username,
                    "opportunity_name": session.opportunity_name,
                    "domain": session.domain,
                    "app_id": session.app_id,
                    "date_range": [str(session.start_date), str(session.end_date)],
                    "overall_result": session.overall_result,
                    "notes": session.notes,
                    "kpi_notes": session.kpi_notes,
                    "created_at": session.created_at.isoformat(),
                    "completed_at": session.completed_at.isoformat() if session.completed_at else None,
                },
                "visit_results": visit_results,
            }
            all_audits.append(audit_data)

        # Create combined export
        export_data = {
            "export_metadata": {
                "export_date": timezone.now().isoformat(),
                "total_audits": len(all_audits),
                "exported_by": request.user.username or request.user.email,
            },
            "audits": all_audits,
        }

        # Return as JSON download
        filename = f'audit_export_all_{timezone.now().strftime("%Y%m%d_%H%M%S")}.json'
        response = HttpResponse(
            content_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

        json.dump(export_data, response, indent=2)
        return response


class AuditImageView(LoginRequiredMixin, View):
    """Serve audit visit images"""

    def get(self, request, blob_id):
        blob_meta = get_object_or_404(BlobMeta, blob_id=blob_id)

        try:
            from django.core.files.storage import default_storage

            file_content = default_storage.open(str(blob_meta.blob_id))

            response = FileResponse(file_content, content_type=blob_meta.content_type, filename=blob_meta.name)
            return response

        except Exception as e:
            return HttpResponse(f"Image not found: {e}", status=404)


class AuditVisitDataView(LoginRequiredMixin, View):
    """AJAX endpoint to fetch visit data without page reload"""

    def get(self, request, session_id):
        session = get_object_or_404(Audit, id=session_id)
        visit_index = int(request.GET.get("visit", 1)) - 1  # Convert from 1-based URL to 0-based index

        # Get all visits explicitly assigned to this session
        # Note: deliver_unit can be NULL, so we don't include it in select_related (INNER JOIN)
        all_visits = session.visits.all().select_related("user", "opportunity").order_by("visit_date")

        existing_results = {result.user_visit_id: result for result in session.results.select_related("user_visit")}

        total_visits = all_visits.count()

        if visit_index < 0:
            visit_index = 0
        elif visit_index >= total_visits:
            visit_index = max(0, total_visits - 1)

        current_visit = None
        if total_visits > 0:
            current_visit = all_visits[visit_index]
            current_visit.audit_result = existing_results.get(current_visit.id)

        # Calculate progress based on assessed images
        from commcare_connect.audit.helpers import calculate_audit_progress

        progress_percentage, assessed_count, total_assessments = calculate_audit_progress(session)

        # Prepare visit data
        visit_data = {
            "visit_index": visit_index,
            "total_visits": total_visits,
            "audited_count": assessed_count,
            "pending_count": total_assessments - assessed_count,
            "progress_percentage": progress_percentage,
            "has_previous": visit_index > 0,
            "has_next": visit_index < total_visits - 1,
            "previous_index": visit_index - 1 if visit_index > 0 else 0,
            "next_index": visit_index + 1 if visit_index < total_visits - 1 else visit_index,
        }

        if current_visit:
            # Get assessments for this visit's images
            audit_result = existing_results.get(current_visit.id)
            assessments_data = []

            if audit_result:
                assessments = Assessment.objects.filter(audit_result=audit_result).select_related("audit_result")
                assessments_by_blob = {a.blob_id: a for a in assessments}

                for idx, image in enumerate(current_visit.images.all()):
                    assessment = assessments_by_blob.get(image.blob_id)
                    if assessment:
                        assessments_data.append(
                            {
                                "id": assessment.id,
                                "blob_id": image.blob_id,
                                "image_url": request.build_absolute_uri(f"/audit/image/{image.blob_id}/"),
                                "question_id": assessment.question_id,
                                "result": assessment.result,
                                "notes": assessment.notes or "",
                                "submitting": False,
                                "noteOpen": False,
                            }
                        )

            visit_data.update(
                {
                    "visit_id": current_visit.id,
                    "visit_date": current_visit.visit_date.strftime("%b %d, %H:%M"),
                    "entity_name": current_visit.entity_name or "No Entity",
                    "location": current_visit.location or "No Location",
                    "image_count": current_visit.images.count(),
                    "audit_result": {
                        "result": current_visit.audit_result.result if current_visit.audit_result else None,
                        "notes": current_visit.audit_result.notes if current_visit.audit_result else "",
                    }
                    if hasattr(current_visit, "audit_result") and current_visit.audit_result
                    else None,
                    "assessments": assessments_data,
                    "images": [
                        {
                            "blob_id": image.blob_id,
                            "url": request.build_absolute_uri(f"/audit/image/{image.blob_id}/"),
                            "counter": idx + 1,
                            "question_id": image.question_id,
                        }
                        for idx, image in enumerate(current_visit.images.all())
                    ],
                }
            )

        return JsonResponse(visit_data)


class AuditCreationWizardView(LoginRequiredMixin, TemplateView):
    """Main audit creation wizard interface"""

    template_name = "audit/audit_creation_wizard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Create New Audit Session"
        return context


class ProgramSearchAPIView(LoginRequiredMixin, View):
    """API endpoint for searching opportunities (renamed from programs for backward compatibility)"""

    def get(self, request):
        query = request.GET.get("q", "").strip()
        limit = min(int(request.GET.get("limit", 20)), 100)  # Cap at 100 results

        print(f"[VIEW DEBUG] query='{query}', limit={limit}")
        print(f"[VIEW DEBUG] user={request.user}")

        facade = ConnectAPIFacade(user=request.user, request=request)
        print("[VIEW DEBUG] facade created")

        facade.authenticate()
        print("[VIEW DEBUG] facade authenticated")

        opportunities = facade.search_opportunities(query, limit)
        print(f"[VIEW DEBUG] opportunities returned: {len(opportunities)}")

        if opportunities:
            for opp in opportunities:
                print(f"[VIEW DEBUG] - ID: {opp.id}, Name: {opp.name}, Visits: {opp.visit_count}")
        else:
            print("[VIEW DEBUG] No opportunities found")

        # Convert opportunities to JSON-serializable format
        opportunities_data = []
        for opportunity in opportunities:
            opportunities_data.append(
                {
                    "id": opportunity.id,
                    "name": opportunity.name,
                    "description": opportunity.description,
                    "program_id": opportunity.program_id,
                    "program_name": opportunity.program_name,
                    "organization_id": opportunity.organization_id,
                    "organization_name": opportunity.organization_name,
                    "start_date": opportunity.start_date.isoformat() if opportunity.start_date else None,
                    "end_date": opportunity.end_date.isoformat() if opportunity.end_date else None,
                    "deliver_app_id": opportunity.deliver_app_id,
                    "deliver_app_domain": opportunity.deliver_app_domain,
                    "deliver_app_cc_app_id": opportunity.deliver_app_cc_app_id,
                    "is_test": opportunity.is_test,
                    "active": opportunity.active,
                    "visit_count": opportunity.visit_count,
                }
            )

        facade.close()

        return JsonResponse(
            {"success": True, "opportunities": opportunities_data, "count": len(opportunities_data), "query": query}
        )


class ProgramOpportunitiesAPIView(LoginRequiredMixin, View):
    """API endpoint for getting opportunities within a program"""

    def get(self, request, program_id):
        try:
            facade = ConnectAPIFacade(user=request.user, request=request)
            if not facade.authenticate():
                return JsonResponse({"error": "Failed to authenticate with data source"}, status=500)

            try:
                opportunities = facade.get_opportunities_by_program(program_id)

                # Convert opportunities to JSON-serializable format
                opportunities_data = []
                for opp in opportunities:
                    opportunities_data.append(
                        {
                            "id": opp.id,
                            "name": opp.name,
                            "description": opp.description,
                            "program_id": opp.program_id,
                            "program_name": opp.program_name,
                            "organization_id": opp.organization_id,
                            "organization_name": opp.organization_name,
                            "start_date": opp.start_date.isoformat() if opp.start_date else None,
                            "end_date": opp.end_date.isoformat() if opp.end_date else None,
                            "deliver_app_id": opp.deliver_app_id,
                            "deliver_app_domain": opp.deliver_app_domain,
                            "deliver_app_cc_app_id": opp.deliver_app_cc_app_id,
                            "is_test": opp.is_test,
                            "active": opp.active,
                        }
                    )

                return JsonResponse(
                    {
                        "success": True,
                        "opportunities": opportunities_data,
                        "count": len(opportunities_data),
                        "program_id": program_id,
                    }
                )

            finally:
                facade.close()

        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


class AuditPreviewAPIView(LoginRequiredMixin, View):
    """API endpoint for previewing audit scope (FLW counts and visit numbers)"""

    def _run_preview_in_background(self, task_id, opportunity_ids, criteria, user=None):
        """Run preview generation in a background thread"""
        progress_tracker = ProgressTracker(task_id=task_id)
        facade = None

        try:
            # Initialize and authenticate facade
            facade = ConnectAPIFacade(user=user)
            if not facade.authenticate():
                progress_tracker.error("Failed to authenticate with data source")
                return

            # Generate preview with progress tracking
            result = preview_audit_sessions(
                facade=facade,
                opportunity_ids=opportunity_ids,
                criteria=criteria,
                progress_tracker=progress_tracker,
            )

            if not result.success:
                progress_tracker.error(result.error)
                return

            # Mark as complete with preview data
            progress_tracker.complete(
                "Preview generated successfully!",
                result_data={
                    "preview_results": result.preview_data,
                    "audit_definition_id": result.audit_definition.id if result.audit_definition else None,
                },
            )

        except Exception as e:
            import traceback

            progress_tracker.error(str(e), traceback.format_exc())
        finally:
            if facade:
                facade.close()

    def post(self, request):
        try:
            data = json.loads(request.body)
            opportunity_ids = data.get("opportunities", [])
            criteria = data.get("criteria", {})

            if not opportunity_ids or not criteria:
                return JsonResponse({"error": "Missing opportunities or criteria"}, status=400)

            # Initialize progress tracker and set initial state in cache
            progress_tracker = ProgressTracker()
            progress_tracker.update(0, 5, "Starting preview generation...", "initializing")

            # Start the preview generation in a background thread
            thread = threading.Thread(
                target=self._run_preview_in_background,
                args=(progress_tracker.task_id, opportunity_ids, criteria, request.user),
                daemon=True,
            )
            thread.start()

            # Return task_id immediately so client can start polling
            return JsonResponse(
                {
                    "success": True,
                    "task_id": progress_tracker.task_id,
                }
            )

        except Exception as e:
            import traceback

            return JsonResponse({"error": str(e), "traceback": traceback.format_exc()}, status=500)


class AuditCreateAPIView(LoginRequiredMixin, View):
    """API endpoint for creating audit sessions and loading data"""

    def _run_audit_creation_in_background(
        self, task_id, opportunity_ids, criteria, auditor_username, user=None, request=None
    ):
        """Run audit creation in a background thread"""
        progress_tracker = ProgressTracker(task_id=task_id)
        facade = None

        try:
            # Initialize and authenticate facade
            facade = ConnectAPIFacade(user=user, request=request)
            if not facade.authenticate():
                progress_tracker.error("Failed to authenticate with data source")
                return

            # Create audit sessions using the service with progress tracking
            result = create_audit_sessions(
                facade=facade,
                opportunity_ids=opportunity_ids,
                criteria=criteria,
                auditor_username=auditor_username,
                progress_tracker=progress_tracker,
            )

            if not result.success:
                progress_tracker.error(result.error)
                return

            # Redirect to the first audit if only one, otherwise to the list
            if result.audits_created == 1:
                redirect_url = reverse_lazy("audit:session_detail", kwargs={"pk": result.first_audit.pk})
            else:
                redirect_url = reverse_lazy("audit:session_list")

            # Mark as complete with result data
            progress_tracker.complete(
                "Audit created successfully!",
                result_data={
                    "redirect_url": str(redirect_url),
                    "audits_created": result.audits_created,
                    "stats": result.stats,
                },
            )

        except Exception as e:
            import traceback

            progress_tracker.error(str(e), traceback.format_exc())
        finally:
            if facade:
                facade.close()

    def post(self, request):
        try:
            data = json.loads(request.body)
            opportunity_ids = data.get("opportunities", [])
            criteria = data.get("criteria", {})

            if not opportunity_ids or not criteria:
                return JsonResponse({"error": "Missing required data"}, status=400)

            # Use username or email as auditor identifier
            auditor_username = request.user.username or request.user.email

            # Initialize progress tracker and set initial state in cache
            progress_tracker = ProgressTracker()
            progress_tracker.update(0, 5, "Starting audit creation...", "initializing")

            # Start the audit creation in a background thread
            thread = threading.Thread(
                target=self._run_audit_creation_in_background,
                args=(progress_tracker.task_id, opportunity_ids, criteria, auditor_username, request.user, request),
                daemon=True,
            )
            thread.start()

            # Return task_id immediately so client can start polling
            return JsonResponse(
                {
                    "success": True,
                    "task_id": progress_tracker.task_id,
                }
            )

        except Exception as e:
            import traceback

            return JsonResponse({"error": str(e), "traceback": traceback.format_exc()}, status=500)


class AuditProgressAPIView(LoginRequiredMixin, View):
    """API endpoint for polling audit creation progress"""

    def get(self, request):
        task_id = request.GET.get("task_id")
        if not task_id:
            return JsonResponse({"error": "Missing task_id"}, status=400)

        progress_tracker = ProgressTracker(task_id=task_id)
        progress_data = progress_tracker.get_progress()

        if progress_data is None:
            return JsonResponse({"error": "Task not found"}, status=404)

        return JsonResponse(progress_data)

    def delete(self, request):
        """Cancel an in-progress audit creation"""
        task_id = request.GET.get("task_id")
        if not task_id:
            return JsonResponse({"error": "Missing task_id"}, status=400)

        progress_tracker = ProgressTracker(task_id=task_id)
        progress_tracker.cancel()

        return JsonResponse({"success": True, "message": "Cancellation requested"})


class DatabaseStatsAPIView(LoginRequiredMixin, View):
    """API endpoint for getting database statistics"""

    def get(self, request):
        try:
            stats = get_database_stats()
            return JsonResponse({"success": True, "stats": stats})

        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


class DatabaseResetAPIView(LoginRequiredMixin, View):
    """API endpoint for resetting audit-related database tables"""

    def post(self, request):
        try:
            deleted = reset_audit_database()
            return JsonResponse({"success": True, "deleted": deleted})

        except Exception as e:
            import traceback

            return JsonResponse({"error": str(e), "traceback": traceback.format_exc()}, status=500)


class DownloadMissingAttachmentsAPIView(LoginRequiredMixin, View):
    """API endpoint for downloading missing attachments for all audit sessions"""

    def post(self, request):
        from commcare_connect.audit.services.database_manager import download_missing_attachments

        try:
            # Create progress tracker
            progress_tracker = ProgressTracker()
            task_id = progress_tracker.task_id

            # Initialize progress with steps
            progress_tracker.steps = [
                {"name": "scanning", "message": "Scanning audit sessions...", "percentage": 0, "status": "pending"},
                {"name": "attachments", "message": "Downloading attachments...", "percentage": 0, "status": "pending"},
                {
                    "name": "assessments",
                    "message": "Regenerating assessments...",
                    "percentage": 0,
                    "status": "pending",
                },
            ]

            # Start download in background thread
            def run_download():
                try:
                    download_missing_attachments(progress_tracker=progress_tracker)
                except Exception as e:
                    import traceback

                    progress_tracker.error(str(e), traceback.format_exc())

            download_thread = threading.Thread(target=run_download, daemon=True)
            download_thread.start()

            return JsonResponse({"success": True, "task_id": task_id})

        except Exception as e:
            import traceback

            return JsonResponse({"error": str(e), "traceback": traceback.format_exc()}, status=500)


class AuditTemplateExportView(LoginRequiredMixin, View):
    """Export an audit definition as JSON"""

    def get(self, request, definition_id):
        from commcare_connect.audit.models import AuditTemplate

        definition = get_object_or_404(AuditTemplate, id=definition_id)
        data = definition.to_dict()

        response = HttpResponse(
            content_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="audit_definition_{definition_id}_'
                f'{timezone.now().strftime("%Y%m%d_%H%M%S")}.json"'
            },
        )

        json.dump(data, response, indent=2)
        return response


class AuditTemplateImportView(LoginRequiredMixin, View):
    """Import an audit definition from JSON"""

    def post(self, request):
        from commcare_connect.audit.models import AuditTemplate

        try:
            uploaded_file = request.FILES.get("file")
            if not uploaded_file:
                return JsonResponse({"error": "No file uploaded"}, status=400)

            data = json.load(uploaded_file)
            definition = AuditTemplate.from_dict(data, user=request.user)
            definition.save()

            return JsonResponse({"success": True, "definition_id": definition.id})

        except Exception as e:
            import traceback

            return JsonResponse({"error": str(e), "traceback": traceback.format_exc()}, status=500)


class BulkAssessmentView(LoginRequiredMixin, DetailView):
    """Bulk assessment interface for rapid image review"""

    model = Audit
    template_name = "audit/bulk_assessment.html"
    context_object_name = "session"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        session = self.get_object()

        # Get filter parameters
        question_id_filter = self.request.GET.get("question_id", "")
        status_filter = self.request.GET.get("status", "all")  # all, pending, pass, fail

        # Get all assessments for this session
        assessments = (
            Assessment.objects.filter(
                audit_result__audit_session=session, assessment_type=Assessment.AssessmentType.IMAGE
            )
            .select_related("audit_result__user_visit")
            .order_by("audit_result__user_visit__visit_date", "question_id")
        )

        # Filter by question_id if specified
        if question_id_filter:
            assessments = assessments.filter(question_id=question_id_filter)

        # Filter by status if specified
        if status_filter == "pending":
            assessments = assessments.filter(result__isnull=True)
        elif status_filter == "pass":
            assessments = assessments.filter(result="pass")
        elif status_filter == "fail":
            assessments = assessments.filter(result="fail")

        # Get unique question_ids for filter dropdown
        question_ids = (
            Assessment.objects.filter(
                audit_result__audit_session=session, assessment_type=Assessment.AssessmentType.IMAGE
            )
            .values_list("question_id", flat=True)
            .distinct()
            .order_by("question_id")
        )

        # Calculate statistics
        all_assessments = (
            Assessment.objects.filter(
                audit_result__audit_session=session, assessment_type=Assessment.AssessmentType.IMAGE
            )
            .select_related("audit_result__user_visit")
            .order_by("audit_result__user_visit__visit_date", "question_id")
        )

        total_assessments = all_assessments.count()
        pending_count = all_assessments.filter(result__isnull=True).count()
        pass_count = all_assessments.filter(result="pass").count()
        fail_count = all_assessments.filter(result="fail").count()

        context.update(
            {
                "assessments": assessments,
                "all_assessments": all_assessments,  # All assessments for visit summaries
                "question_ids": question_ids,
                "selected_question_id": question_id_filter,
                "selected_status": status_filter,
                "total_assessments": total_assessments,
                "pending_count": pending_count,
                "pass_count": pass_count,
                "fail_count": fail_count,
            }
        )

        return context


class AssessmentUpdateView(LoginRequiredMixin, View):
    """AJAX endpoint for updating individual assessments"""

    def post(self, request, assessment_id):
        try:
            result = request.POST.get("result")  # 'pass', 'fail', or None/'' to clear
            notes = request.POST.get("notes")  # Can be None if not provided

            assessment = get_object_or_404(Assessment, id=assessment_id)

            # Update assessment result if provided
            if result in ["pass", "fail"]:
                assessment.result = result
                assessment.assessed_at = timezone.now()
            elif result == "":  # Clear result
                assessment.result = None
                assessment.assessed_at = None

            # Update notes if provided
            if notes is not None:
                assessment.notes = notes

            assessment.save()

            # Return updated statistics
            session = assessment.audit_result.audit_session
            stats = self._get_session_stats(session)

            return JsonResponse(
                {"success": True, "assessment_id": assessment_id, "result": assessment.result, "stats": stats}
            )

        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)

    def _get_session_stats(self, session):
        """Calculate assessment statistics for a session"""
        assessments = Assessment.objects.filter(
            audit_result__audit_session=session, assessment_type=Assessment.AssessmentType.IMAGE
        )

        return {
            "total": assessments.count(),
            "pending": assessments.filter(result__isnull=True).count(),
            "pass": assessments.filter(result="pass").count(),
            "fail": assessments.filter(result="fail").count(),
        }


class VisitResultUpdateView(LoginRequiredMixin, View):
    """AJAX endpoint for updating visit-level result (AuditResult)"""

    def post(self, request, visit_id):
        try:
            from commcare_connect.audit.models import AuditResult

            result = request.POST.get("result")  # 'pass', 'fail', or '' to clear

            # Allow empty string to clear the result
            if result == "":
                result = None
            elif result not in ["pass", "fail"]:
                return JsonResponse({"error": "Invalid result. Must be 'pass', 'fail', or empty to clear"}, status=400)

            audit_result = get_object_or_404(AuditResult, user_visit_id=visit_id)
            audit_result.result = result
            audit_result.save()

            return JsonResponse({"success": True, "visit_id": visit_id, "result": result})

        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


class ApplyAssessmentResultsView(LoginRequiredMixin, View):
    """Apply assessment results to visit-level AuditResults"""

    def post(self, request, session_id):
        try:
            session = get_object_or_404(Audit, id=session_id)

            # Get all audit results for this session
            audit_results = AuditResult.objects.filter(audit_session=session).prefetch_related("assessments")

            updated_count = 0
            visits_failed = 0
            visits_passed = 0

            for audit_result in audit_results:
                # Check if any assessment failed
                assessments = audit_result.assessments.all()
                has_failure = assessments.filter(result="fail").exists()
                all_assessed = not assessments.filter(result__isnull=True).exists()

                old_result = audit_result.result

                # Apply logic: if any assessment failed, fail the visit
                if has_failure:
                    audit_result.result = "fail"
                    visits_failed += 1
                elif all_assessed and assessments.exists():
                    # All assessments passed
                    audit_result.result = "pass"
                    visits_passed += 1

                if old_result != audit_result.result:
                    updated_count += 1
                    audit_result.save()

            return JsonResponse(
                {
                    "success": True,
                    "updated_count": updated_count,
                    "visits_passed": visits_passed,
                    "visits_failed": visits_failed,
                    "message": f"Updated {updated_count} visit results based on assessments",
                }
            )

        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)
