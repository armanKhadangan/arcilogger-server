import type { PersonOut } from '@/lib/api-types';
import { DEVICES, need } from '@/test/samples';

import {
  NO_DEPARTMENT,
  personChanges,
  personCreateBody,
  personFormValues,
  validatePerson,
} from './persons-model';

// Captured: Finans (department 1), employee number 1002, with an e-mail address.
const can: PersonOut = need(
  DEVICES.persons.items.find((person) => person.name === 'Can Öztürk'),
  'Can Öztürk',
);

describe('person form', () => {
  it('starts empty and active for a new person, filled for an existing one', () => {
    expect(personFormValues(null)).toEqual({
      name: '',
      department: NO_DEPARTMENT,
      title: '',
      email: '',
      employeeNo: '',
      notes: '',
      active: true,
      usageExcluded: false,
    });
    expect(personFormValues(can)).toMatchObject({
      name: 'Can Öztürk',
      department: '1',
      title: 'Finans Müdürü',
      email: 'can.ozturk@example.com',
      employeeNo: '1002',
      active: true,
      usageExcluded: false,
    });
    expect(personFormValues({ ...can, usage_excluded: true }).usageExcluded).toBe(true);
  });

  it('checks the name, e-mail and text fields', () => {
    expect(validatePerson(personFormValues(null))).toEqual({ name: 'validation.required' });
    expect(
      validatePerson({
        ...personFormValues(null),
        name: 'Ayşe\u202eYılmaz',
        email: 'ayse@',
        title: 'x'.repeat(201),
        employeeNo: '10\u200b02',
      }),
    ).toEqual({
      name: 'validation.controlCharacters',
      email: 'validation.email',
      title: 'validation.tooLong',
      employeeNo: 'validation.controlCharacters',
    });
    expect(validatePerson({ ...personFormValues(can), notes: 'satır 1\nsatır 2' })).toEqual({});
    expect(validatePerson({ ...personFormValues(can), notes: 'x'.repeat(2001) })).toEqual({
      notes: 'validation.tooLong',
    });
    expect(validatePerson({ ...personFormValues(can), employeeNo: '9'.repeat(65) })).toEqual({
      employeeNo: 'validation.tooLong',
    });
  });

  it('creates with the given fields only', () => {
    expect(
      personCreateBody({
        ...personFormValues(null),
        name: '  Burak Çelik ',
        department: '6',
        employeeNo: ' 2002 ',
      }),
    ).toEqual({ name: 'Burak Çelik', department_id: 6, employee_no: '2002' });
    // Left out of the usage reports from the start: sent only when ticked (default false).
    expect(
      personCreateBody({ ...personFormValues(null), name: 'Ece', usageExcluded: true }),
    ).toEqual({ name: 'Ece', usage_excluded: true });
  });

  it('changes only what was edited; a cleared field and "no department" are null', () => {
    const values = personFormValues(can);
    expect(personChanges(can, values)).toEqual({});
    expect(
      personChanges(can, { ...values, department: NO_DEPARTMENT, email: ' ', active: false }),
    ).toEqual({ department_id: null, email: null, active: false });
    expect(personChanges(can, { ...values, title: 'Fabrika Müdürü' })).toEqual({
      title: 'Fabrika Müdürü',
    });
    expect(personChanges(can, { ...values, usageExcluded: true })).toEqual({
      usage_excluded: true,
    });
    const excluded = { ...can, usage_excluded: true };
    expect(personChanges(excluded, personFormValues(excluded))).toEqual({});
    expect(
      personChanges(excluded, { ...personFormValues(excluded), usageExcluded: false }),
    ).toEqual({ usage_excluded: false });
  });
});
