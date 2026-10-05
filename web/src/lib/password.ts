const MIN_PASSWORD_LENGTH = 10;

/** Mirrors the server-side policy so the person is told before they submit.
 *  The server remains the authority - this is courtesy, not enforcement (it
 *  does not know the server's list of common passwords, for one). */
export function passwordIssues(password: string, email?: string, name?: string): string[] {
  const issues: string[] = [];
  if (password.length < MIN_PASSWORD_LENGTH) {
    issues.push(`At least ${MIN_PASSWORD_LENGTH} characters`);
  }
  if (new Set(password).size < 5) issues.push('A greater variety of characters');
  const lowered = password.toLowerCase();
  if (email) {
    const local = email.split('@')[0].toLowerCase();
    if (local.length >= 4 && lowered.includes(local)) issues.push('Must not contain your email');
  }
  if (name) {
    for (const part of name.toLowerCase().split(/\s+/)) {
      if (part.length >= 4 && lowered.includes(part)) {
        issues.push('Must not contain your name');
        break;
      }
    }
  }
  return issues;
}
