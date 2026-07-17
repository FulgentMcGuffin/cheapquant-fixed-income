---
name: validate-skill
description: Validate Claude skill configuration files for correct setup
---

# Validate Claude Skills

Checks whether Claude skill files are correctly configured. Validates YAML frontmatter, required fields, naming conventions, and structure.

## Usage

```
/validate-skill [file-or-directory]
```

**Examples:**
- `/validate-skill .claude/skills/` — Validate all skills in the project
- `/validate-skill .claude/skills/my-skill.md` — Validate a specific skill file

If no path is provided, validates `.claude/skills/` by default.

## What It Checks

- ✓ Valid YAML frontmatter
- ✓ Required fields (`name`, `description`)
- ✓ Kebab-case naming convention
- ✓ Description length (max 200 chars)
- ✓ Valid skill type and agent types
- ✓ Tool and agent references
- ✓ Sufficient body content

## Example Output

```
Validating .claude/skills/...
  ✓ Valid (2 skills):
    - validate-skill.md
    - my-custom-skill.md

  ✗ Invalid (1 skill):
    - broken-skill.md:
      • Missing required field: description
      • Invalid 'name' format: 'BrokenSkill' (use kebab-case)
```

## Implementation

Runs the Python validation script at `scripts/validate_skill.py`. If you modify skill files, run this command to ensure they're properly formatted before committing.
