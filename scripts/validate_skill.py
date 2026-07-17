#!/usr/bin/env python3
"""Validate Claude skill configuration files.

Checks for:
- Valid YAML frontmatter
- Required fields (name, description, etc.)
- Proper formatting and structure
- Tool references (if applicable)
- Agent type references (if applicable)
"""

import sys
import re
from pathlib import Path
from typing import Optional
import yaml


def validate_skill_file(file_path: Path) -> tuple[bool, list[str]]:
    """Validate a single skill file.

    Returns:
        (is_valid, list_of_errors)
    """
    errors = []

    if not file_path.exists():
        return False, [f"File not found: {file_path}"]

    if not file_path.suffix.lower() in ['.md', '.yaml', '.yml']:
        return False, [f"Invalid file extension: {file_path.suffix}"]

    try:
        content = file_path.read_text(encoding='utf-8')
    except Exception as e:
        return False, [f"Failed to read file: {e}"]

    # For Markdown files, extract frontmatter
    if file_path.suffix.lower() == '.md':
        if not content.startswith('---'):
            errors.append("Missing frontmatter (should start with ---)")
            return False, errors

        # Extract frontmatter
        parts = content.split('---', 2)
        if len(parts) < 3:
            errors.append("Invalid frontmatter: not properly closed with ---")
            return False, errors

        frontmatter_text = parts[1].strip()
        try:
            metadata = yaml.safe_load(frontmatter_text)
        except yaml.YAMLError as e:
            errors.append(f"Invalid YAML in frontmatter: {e}")
            return False, errors

        body = parts[2].strip()
    else:
        # For YAML files, treat whole content as metadata
        try:
            metadata = yaml.safe_load(content)
        except yaml.YAMLError as e:
            errors.append(f"Invalid YAML: {e}")
            return False, errors
        body = None

    # Validate required fields
    if not isinstance(metadata, dict):
        errors.append("Metadata must be a YAML dictionary")
        return False, errors

    required_fields = ['name', 'description']
    for field in required_fields:
        if field not in metadata:
            errors.append(f"Missing required field: {field}")
        elif not isinstance(metadata.get(field), str) or not metadata.get(field).strip():
            errors.append(f"Field '{field}' must be a non-empty string")

    # Validate name format (kebab-case)
    if 'name' in metadata:
        name = metadata['name']
        if not re.match(r'^[a-z0-9]+(-[a-z0-9]+)*$', name):
            errors.append(f"Invalid 'name' format: '{name}' (use kebab-case: a-z, 0-9, hyphens only)")

    # Validate description length
    if 'description' in metadata:
        desc = metadata['description']
        if len(desc) > 200:
            errors.append(f"Description too long ({len(desc)} chars, max 200)")

    # Check for type field if present
    if 'type' in metadata:
        valid_types = ['skill', 'agent', 'integration']
        if metadata['type'] not in valid_types:
            errors.append(f"Invalid 'type': {metadata['type']} (must be one of: {', '.join(valid_types)})")

    # If body exists, basic sanity checks
    if body:
        if len(body) < 50:
            errors.append("Body content is very short (should have substantial instructions)")

    # Check for common issues
    if 'tools' in metadata:
        if not isinstance(metadata['tools'], (list, dict)):
            errors.append("'tools' field must be a list or dictionary")

    if 'agent_type' in metadata:
        valid_agents = ['claude', 'explore', 'general-purpose', 'plan', 'statusline-setup']
        if metadata['agent_type'] not in valid_agents:
            errors.append(f"Invalid 'agent_type': {metadata['agent_type']}")

    is_valid = len(errors) == 0
    return is_valid, errors


def validate_skills_directory(dir_path: Path) -> dict:
    """Validate all skills in a directory.

    Returns:
        {'valid': [...], 'invalid': {...}}
    """
    results = {'valid': [], 'invalid': {}}

    if not dir_path.is_dir():
        print(f"Error: {dir_path} is not a directory")
        return results

    skill_files = list(dir_path.glob('*.md')) + list(dir_path.glob('*.yaml')) + list(dir_path.glob('*.yml'))

    if not skill_files:
        print(f"No skill files found in {dir_path}")
        return results

    for skill_file in sorted(skill_files):
        is_valid, errors = validate_skill_file(skill_file)

        if is_valid:
            results['valid'].append(skill_file.name)
        else:
            results['invalid'][skill_file.name] = errors

    return results


def main():
    if len(sys.argv) < 2:
        print("Usage: validate_skill.py <file_or_directory> [<file_or_directory> ...]")
        print()
        print("Validates Claude skill configuration files (.md with YAML frontmatter or .yaml)")
        sys.exit(1)

    all_valid = True

    for target in sys.argv[1:]:
        path = Path(target)

        if path.is_file():
            print(f"Validating {path.name}...")
            is_valid, errors = validate_skill_file(path)

            if is_valid:
                print(f"  ✓ Valid")
            else:
                print(f"  ✗ Invalid:")
                for error in errors:
                    print(f"    - {error}")
                all_valid = False

        elif path.is_dir():
            print(f"Validating skills in {path}...")
            results = validate_skills_directory(path)

            if results['valid']:
                print(f"  ✓ Valid ({len(results['valid'])} skills):")
                for name in results['valid']:
                    print(f"    - {name}")

            if results['invalid']:
                print(f"  ✗ Invalid ({len(results['invalid'])} skills):")
                for name, errors in results['invalid'].items():
                    print(f"    - {name}:")
                    for error in errors:
                        print(f"      • {error}")
                all_valid = False

        else:
            print(f"Error: {path} not found")
            all_valid = False

        print()

    sys.exit(0 if all_valid else 1)


if __name__ == '__main__':
    main()
