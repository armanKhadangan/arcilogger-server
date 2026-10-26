/** What a form does with a stored secret: keep it, replace it, or delete it. */
export interface SecretEdit {
  /** The new value (only while `editing`); never a stored value. */
  value: string;
  editing: boolean;
  clear: boolean;
}

export const SECRET_KEPT: SecretEdit = { value: '', editing: false, clear: false };

/** The form sends something for this secret (a new value or its deletion). */
export function secretChanged(edit: SecretEdit): boolean {
  return edit.clear || (edit.editing && edit.value !== '');
}
