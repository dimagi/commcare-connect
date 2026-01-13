# EOI/RFP Solicitations System - CommCare Connect

## 🎉 V1 IMPLEMENTATION COMPLETE - Ready for QA

**Status**: V1 implementation complete with all core features functional, this file is out of date compared to latest code and the latest code should be viewed as the source of truth.
**Last Updated**: August 17, 2025

## System Overview

A comprehensive two-phase procurement system for CommCare Connect:

1. **Expression of Interest (EOI)** - Initial screening phase
2. **Request for Proposal (RFP)** - Detailed proposal phase for EOI winners

## ✅ V1 Features Implemented

1. **Program-owned Solicitations**: Programs can create and publish EOIs/RFPs
2. **Public Viewing**: Professional public pages to showcase active solicitations
3. **Authenticated Responses**: Organizations submit responses with file attachments
4. **Admin Review System**: Complete review workflow with scoring and status management
5. **Dynamic Question Builder**: Configurable questions with 5 question types
6. **Professional UI**: Consistent styling matching existing Connect design patterns

**Note**: This is a V1 implementation. Some minor features or edge cases may need refinement during QA testing.

## Technical Implementation

**AI-Generated Codebase**: This system was implemented using AI assistance, following established patterns from the existing human-authored codebase. All code mirrors patterns found in the `opportunity`, `program`, and `users` apps.

**Architecture Principles**:

- **Pattern Consistency**: Follows existing Connect patterns (models, views, templates, URLs)
- **Professional UI**: Uses established Tailwind CSS classes and component patterns, use Alpine.js for javascript
- **Frameworks**: Uses Alpine.js for javascript and Crispy forms for data entry forms.
- **Database Integration**: Proper foreign keys, indexes, and migrations
- **Security**: Same authentication/authorization patterns as existing features

## Core Data Models

### 1. Solicitation (unified model for EOI/RFP)

- `title` - Name of the solicitation
- `description` - Rich text description of the program
- `target_population` - Who will be served
- `scope_of_work` - What work needs to be done
- `expected_start_date` / `expected_end_date` - Timeline
- `estimated_scale` - Number of beneficiaries/FLWs
- `application_deadline` - When responses are due
- `solicitation_type` - 'eoi' or 'rfp'
- `status` - draft, active, completed, closed
- `is_publicly_listed` - Boolean to control public listing visibility
- `program` - Foreign key to Program (owner)
- `created_by` - Admin who created it
- `parent_solicitation` - Self-referencing FK (RFP references parent EOI)
- `attachments` - File field for supporting documents

### 2. SolicitationResponse

- `solicitation` - Foreign key to Solicitation
- `organization` - Foreign key to Organization (responding org)
- `submitted_by` - User who submitted the response
- `submission_date` - When submitted
- `responses` - JSON field for flexible question/answer storage
- `status` - draft, submitted, under_review, accepted, rejected
- `attachments` - Related ResponseAttachment objects

### 3. SolicitationQuestion

- `solicitation` - Foreign key to Solicitation
- `question_text` - The question being asked
- `question_type` - text, textarea, number, file, multiple_choice
- `is_required` - Boolean for validation
- `options` - JSON field for multiple choice options
- `order` - Integer for question ordering

### 4. SolicitationReview

- `response` - Foreign key to SolicitationResponse
- `reviewer` - User who conducted the review
- `score` - Numeric score (0-100)
- `recommendation` - accept, reject, needs_more_info
- `notes` - Text field for reviewer comments
- `review_date` - When review was completed

### 5. ResponseAttachment

- `response` - Foreign key to SolicitationResponse
- `file` - FileField for uploaded documents
- `filename` - Original filename
- `uploaded_at` - Timestamp

## URL Structure

### Public URLs (no authentication)

- `/solicitations/` - Public listing of all active solicitations
- `/solicitations/eoi/` - EOI-only listing
- `/solicitations/rfp/` - RFP-only listing
- `/solicitations/<id>/` - Individual solicitation detail page

### Authenticated URLs (organization users)

- `/solicitations/<id>/respond/` - Submit response to solicitation
- `/solicitations/response/<id>/success/` - Response submission confirmation

### Program Manager URLs (within program context)

- `/a/<org>/program/<id>/solicitations/` - Program solicitation dashboard
- `/a/<org>/program/<id>/solicitations/create/` - Create new solicitation
- `/a/<org>/program/<id>/solicitations/<id>/edit/` - Edit solicitation
- `/a/<org>/program/<id>/solicitations/<id>/responses/` - View responses to solicitation
- `/a/<org>/program/<id>/solicitations/response/<id>/review/` - Review individual response

### Admin URLs (superuser only)

- `/solicitations/admin-overview/` - Cross-program solicitation overview

## Workflow Examples

### EOI → RFP → Opportunity Workflow

```
Program Manager creates EOI → Organizations submit responses
↓
Program Manager reviews EOI responses → Accepts subset
↓
Program Manager creates RFP for accepted orgs → RFP response process
↓
Program Manager reviews RFP responses → Creates Opportunities (out of scope)
```

## ✅ IMPLEMENTATION SUMMARY

**All 4 phases completed successfully**. The solicitations system is fully functional with:

### Core Features Implemented:

- **Public Pages**: Professional solicitation listings and detail pages
- **Response System**: Dynamic forms with file attachments and draft saving
- **Admin Review**: Complete review workflow with scoring and status management
- **Authoring System**: Full CRUD for solicitations with dynamic question builder
- **UI/UX**: Consistent professional design matching existing Connect patterns

### Technical Architecture:

- **Models**: 6 core models with proper relationships and validation
- **Views**: Mix of class-based views and AJAX endpoints following Connect patterns
- **Templates**: Professional responsive design using existing Tailwind patterns
- **JavaScript**: Dynamic question builder with real-time AJAX updates
- **Tables**: Django Tables2 integration for consistent data display
- **Forms**: Dynamic form generation with validation and error handling

---

## 🔍 QA TESTING GUIDE

### Testing Approach

**IMPORTANT**: The `solicitations` app is **AI-generated** code, while the rest of the CommCare Connect codebase is **human-authored**. For debugging and styling issues, use the existing codebase as the authoritative reference.

### Reference Apps for Pattern Comparison:

- **`opportunity/`**: Best reference for similar workflow patterns
- **`program/`**: Reference for program management UI patterns
- **`users/`**: Reference for authentication and permission patterns
- **Templates in `templates/`**: Reference for consistent UI components

## 📋 Recommended Testing Order

### Phase 1: Public Components (No Authentication)

```
1. Public solicitation listing (/solicitations/)
   - Test filtering by type (EOI vs RFP)
   - Verify only active solicitations show
   - Check responsive design
   - Validate professional appearance

2. Public solicitation detail pages (/solicitations/<id>/)
   - Test all solicitation types
   - Verify questions display correctly
   - Check file attachment display
   - Test "Apply Now" button redirects
```

### Phase 2: Solicitation Authoring (Program Manager)

```
1. Solicitation creation
   - Test form validation
   - Test dynamic question builder
   - Verify all question types work
   - Test draft/publish workflow

2. Solicitation editing
   - Test loading existing questions
   - Test question reordering
   - Test question deletion
   - Verify changes save correctly
```

### Phase 3: Response Submission (Organization Users)

```
1. Response submission flow
   - Test dynamic form generation
   - Test file uploads
   - Test draft saving
   - Test final submission

2. Response management
   - Test viewing submitted responses
   - Test editing draft responses
   - Verify organization context
```

### Phase 4: Review & Management (Admin/Program Manager)

```
1. Response review interface
   - Test response listing and filtering
   - Test individual review workflow
   - Test scoring system
   - Test status updates

2. Dashboard and statistics
   - Test response statistics
   - Test table functionality
   - Test admin overview
```

### Debugging Recommendations:

#### For Styling Issues:

1. **Compare with existing templates** in `templates/opportunity/` or `templates/program/`
2. **Check Tailwind classes** against existing usage in human-authored templates
3. **Verify component patterns** match existing card/table/button implementations

#### For Functional Issues:

1. **Compare views.py patterns** with `opportunity/views.py` or `program/views.py`
2. **Check URL patterns** against existing app URL structures
3. **Verify model relationships** follow same patterns as existing models

#### For JavaScript Issues:

1. **Check existing JavaScript** in `static/js/` for reference patterns
2. **Verify AJAX patterns** match existing implementations
3. **Compare event handling** with existing dynamic features

### Known Implementation Details:

- **Django Tables2** used for all data tables (following existing patterns)
- **AJAX endpoints** for question management (following opportunity app patterns)
- **File handling** mirrors existing attachment systems
- **Permission mixins** follow existing authentication patterns

## Additional Testing Considerations

### UI/UX Consistency Testing

```
✅ Compare against existing Connect pages:
- Button styles should match opportunity pages
- Table formatting should match program dashboards
- Form styling should match user management forms
- Card layouts should match existing dashboard patterns
```

### Permission & Security Testing

```
✅ Verify access controls:
- Anonymous users: only public pages
- Organization users: can respond to solicitations
- Program managers: can manage their program's solicitations
- Superusers: can access admin overview
```

### Data Integrity Testing

```
✅ Test database operations:
- Question ordering and relationships
- File upload and storage
- Draft saving and retrieval
- Response submission workflow
- Review scoring system
```

---

## 🚀 V1 READY FOR QA

The solicitations system V1 is **complete and ready for comprehensive QA testing**. All core features are implemented and functional. The codebase follows established Connect patterns and should integrate seamlessly with the existing system.
