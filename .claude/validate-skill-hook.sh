#!/bin/bash
# Hook script for /validate-skill command
# Invoked by Claude Code when user submits a prompt

input=$(cat)

# Try to extract the user prompt from various possible hook input formats
prompt=$(echo "$input" | jq -r '.user_prompt // .prompt // empty' 2>/dev/null)

# If jq failed or prompt is empty, try reading from stdin directly
if [ -z "$prompt" ]; then
  prompt=$(echo "$input" | head -1)
fi

# Check if this is a validate-skill command
if [[ "$prompt" =~ ^/validate-skill ]]; then
  # Extract path if provided, default to .claude/skills/
  path=$(echo "$prompt" | sed 's|^/validate-skill[[:space:]]*||' | xargs)

  if [ -z "$path" ]; then
    path=".claude/skills/"
  fi

  # Run the validator and capture output
  output=$(python scripts/validate_skill.py "$path" 2>&1)
  exit_code=$?

  # Output JSON response to block normal prompt submission
  cat <<EOF
{
  "continue": false,
  "systemMessage": "Skill Validator\n\n$output"
}
EOF
  exit $exit_code
else
  # Not a validate-skill command, continue with normal processing
  echo '{"continue": true}'
  exit 0
fi
