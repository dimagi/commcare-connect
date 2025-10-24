# Pull Request Guidelines

This document outlines best practices for creating and reviewing pull requests in the CommCare Connect project.

## Table of Contents

- [Creating Effective Pull Requests](#creating-effective-pull-requests)
- [Giving Quality Code Reviews](#giving-quality-code-reviews)

## Creating Effective Pull Requests

### What Makes a PR Easy to Review?

#### 1. Comprehensive Descriptions

- **Include demos and screenshots**: Visual aids help reviewers understand the changes quickly
- **Reference Jira tickets**: Include links to tickets with comprehensive summaries and motivation for the change
- **Link to staging environments**: Provide test/demo environment links if needed where reviewers can interact with the feature

#### 2. Manageable Size

- **Keep PRs small**: Smaller pull requests are much easier to review thoroughly
- **Break large features into multiple PRs**: Consider splitting complex features across several focused pull requests
- **Each PR should have a single, clear purpose**

#### 3. Clean Git History

- **Use meaningful commit messages**: Each commit should tell a clear story
- **Rewrite history when beneficial**: Use git rebase to make the PR easier to read
- **Logical commit progression**: Organize commits in a way that makes the development process clear

#### 4. Test Coverage

- **Include comprehensive tests**: Tests give reviewers confidence in the changes
- **Test edge cases**: Demonstrate that you've considered various scenarios
- **Update existing tests**: Ensure all tests pass and are relevant

#### 5. Environment Setup as applicable

- **Provide setup instructions**: Include ready-to-go instructions for getting the environment set up
- **Document dependencies**: List any new dependencies or configuration changes
- **Include migration instructions**: If database changes are involved, provide clear migration steps

#### 6. Review Guidance

- **Include review instructions**: In the PR description, provide notes about:
  - Whether to review commits individually vs. looking at all code together
  - Areas that need special attention
  - The order in which to review changes
- **Self-review first**: Review your own code and call attention to:
  - Difficult or unexpected implementation choices
  - Areas where you're unsure about the approach
  - Known limitations or trade-offs

#### 7. AI Review Integration

- **Use AI reviews as a first pass**: Let automated tools catch basic issues first
- **Respond to AI feedback**: Address AI review comments before requesting human review

#### 8. Code Standards

- **Follow the project styleguide**: Ensure code adheres to established conventions
- **Consistent formatting**: Use project linting and formatting tools
- **Clear naming conventions**: Use descriptive variable and function names

## Giving Quality Code Reviews

### Core Principles

#### 1. Be Explicit About Requirements

- **Distinguish between blocking and optional feedback**:
  - Use clear language: "This must be fixed before merge" vs. "Consider this improvement"
  - Mark optional suggestions as "nit:" or "optional:"
- **Specify scope clearly**:
  - "This should be addressed in this PR" vs. "This could be a follow-up PR"
  - Help prioritize what needs immediate attention

#### 2. Provide Specific Guidance

- **Guide toward resolution, not suppression**:
  - Instead of "suppress this warning," explain how to properly fix the underlying issue
  - Provide specific suggestions for improvement
- **Enforce code standards consistently**:
  - Reference style guides and project conventions
  - Explain the reasoning behind standards when not obvious

#### 3. Think Beyond the Code

- **Question big-picture design and architecture**:
  - Don't hesitate to challenge fundamental approaches if they seem problematic
  - Consider the broader scope of the feature and how it fits into the system
- **Understand the complete context**:
  - Ask questions about requirements and use cases
  - Consider maintainability and future extensibility

#### 4. Review Process Best Practices

- **Submit feedback early and often**:
  - Don't wait until you've reviewed everything to provide initial feedback
  - Early feedback can save time by catching major issues quickly
- **Use voice communication when needed**:
  - For complex discussions or standoffs, consider a quick call
  - Voice chat helps with context and big-picture questions
  - Can resolve misunderstandings faster than written back-and-forth

#### 5. Communication Style

- **Be constructive and respectful**:
  - Focus on the code, not the person
  - Explain the "why" behind your suggestions
- **Ask questions to understand**:
  - "Can you help me understand why you chose this approach?"
  - "What's the reasoning behind this design decision?"
- **Acknowledge good work**:
  - Call out clever solutions and well-written code
  - Recognize improvements and good practices

## Additional Resources

- https://google.github.io/eng-practices/review/developer/

---

_Remember: Good PR practices benefit everyone. They make code reviews more effective, reduce bugs, and help maintain a high-quality codebase._
