import type { FormContent } from "./content";
import type { WaitlistField } from "./waitlist";

/*
 * Meldung zu einer Fehlerantwort in der Sprache der Seite. Route und Edge
 * Function antworten deutsch; entscheidend sind hier nur `field` und Status,
 * damit beide Sprachen dieselben Faelle kennen.
 */
export function errorMessage(
  errors: FormContent["errors"],
  status: number,
  field: WaitlistField | undefined,
  emailEmpty: boolean,
): string {
  if (status === 422) {
    switch (field) {
      case "email":
        return emailEmpty ? errors.emailMissing : errors.emailInvalid;
      case "name":
        return errors.name;
      case "role":
        return errors.role;
      case "platform":
        return errors.platform;
      case "consent":
        return errors.consent;
    }
  }
  switch (status) {
    case 400:
      return errors.unreadable;
    case 403:
      return errors.forbidden;
    case 413:
      return errors.tooLarge;
    case 429:
      return errors.rateLimited;
    case 503:
      return errors.unavailable;
    default:
      return errors.generic;
  }
}
