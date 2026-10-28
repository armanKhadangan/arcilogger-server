import type { SourceHealth } from '@/lib/sources-types';
import { SOURCES } from '@/test/samples';
import { completeConfig, capturedConfig, freshConfig, stampTest } from '@/test/w4-fixtures';

import {
  arrivals,
  CHECKED_STEPS,
  firewallNotes,
  firstOpenStep,
  isWizardStep,
  nextStep,
  STEP_LINKS,
  stepState,
  verifiedStampTest,
  WIZARD_STEPS,
  wizardStates,
} from './wizard-model';

/*
 * The first-run wizard's states (Wave 4 contract 2.8.4): each step's state from the
 * configuration and the stamp tests, the first open step, and "skip", which stores nothing.
 */

const done = (input: Parameters<typeof wizardStates>[0]) =>
  Object.entries(wizardStates(input))
    .filter(([, state]) => state.done)
    .map(([step]) => step);

describe('wizard steps', () => {
  it('are the contract steps in order, each opening its Ayarlar page in wizard mode', () => {
    expect(WIZARD_STEPS).toEqual([
      'admin',
      'site',
      'sources',
      'kamusm_test',
      'kamusm_production',
      'replica',
      'smtp',
      'summary',
    ]);
    expect(CHECKED_STEPS).not.toContain('summary');
    expect(STEP_LINKS).toEqual({
      site: '/settings/general?wizard=1',
      sources: '/settings/sources?wizard=1',
      kamusm_test: '/settings/kamusm?step=test&wizard=1',
      kamusm_production: '/settings/kamusm?step=production&wizard=1',
      replica: '/settings/replica?wizard=1',
      smtp: '/settings/email?wizard=1',
    });
    expect(isWizardStep('kamusm_test')).toBe(true);
    expect(isWizardStep('test')).toBe(false);
    expect(isWizardStep(null)).toBe(false);
  });
});

describe('every step’s state from the configuration', () => {
  it('a fresh installation: only the administrator step is done', () => {
    const states = wizardStates({ config: freshConfig(), tested: false });
    expect(done({ config: freshConfig(), tested: false })).toEqual(['admin']);
    expect(states.site.missing).toEqual([{ key: 'setupWizard.missing.siteProblems' }]);
    expect(states.sources.missing).toEqual([{ key: 'setupWizard.missing.noSource' }]);
    expect(states.kamusm_test.missing).toEqual([{ key: 'setupWizard.missing.noTest' }]);
    expect(states.kamusm_production.missing).toEqual([
      { key: 'setupWizard.missing.notProduction' },
    ]);
    expect(states.replica.missing).toEqual([{ key: 'setupWizard.missing.replicaOff' }]);
    expect(states.smtp.missing).toEqual([
      { key: 'setupWizard.missing.smtpServer' },
      { key: 'setupWizard.missing.smtpSender' },
    ]);
  });

  it('the captured configuration: site, sources and replica are done', () => {
    expect(done({ config: capturedConfig(), tested: false })).toEqual([
      'admin',
      'site',
      'sources',
      'replica',
    ]);
  });

  it('everything set: every step is done', () => {
    expect(done({ config: completeConfig(), tested: false })).toEqual([...CHECKED_STEPS]);
  });

  it('site: an error of site_name or clock.* keeps it open, a warning does not', () => {
    const config = capturedConfig();
    expect(config.problems.map((item) => [item.level, item.key])).toEqual([
      ['warning', 'clock.ntp_server'],
    ]);
    expect(stepState('site', { config, tested: false }).done).toBe(true);
    const error = (key: string) => ({
      level: 'error' as const,
      key,
      message_tr: 'x',
      message_en: 'x',
    });
    config.problems = [error('clock.fallback_server')];
    expect(stepState('site', { config, tested: false }).done).toBe(false);
    config.problems = [error('site_name')];
    expect(stepState('site', { config, tested: false }).done).toBe(false);
    // An error of another key is not the site step's.
    config.problems = [error('arcilogger.toml')];
    expect(stepState('site', { config, tested: false }).done).toBe(true);
  });

  it('sources: at least one enabled configured source', () => {
    const config = capturedConfig();
    for (const source of config.sections.sources) source.enabled = false;
    expect(stepState('sources', { config, tested: false })).toEqual({
      done: false,
      missing: [{ key: 'setupWizard.missing.noEnabled' }],
    });
    const [, second] = config.sections.sources;
    if (second) second.enabled = true;
    expect(stepState('sources', { config, tested: false }).done).toBe(true);
  });

  it('Kamu SM test: a verified stamp test, or the production server already', () => {
    const config = freshConfig();
    expect(stepState('kamusm_test', { config, tested: true }).done).toBe(true);
    config.sections.tsa.server = 'test';
    expect(stepState('kamusm_test', { config, tested: false }).done).toBe(false);
    config.sections.tsa.server = 'production';
    expect(stepState('kamusm_test', { config, tested: false }).done).toBe(true);
  });

  it('a stamp test counts as Ayarlar → Kamu SM counts it: succeeded and verified', () => {
    expect(verifiedStampTest([])).toBe(false);
    expect(verifiedStampTest([stampTest()])).toBe(true);
    expect(verifiedStampTest([stampTest('succeeded', false)])).toBe(false);
    expect(verifiedStampTest([stampTest('failed')])).toBe(false);
    expect(verifiedStampTest([{ ...stampTest(), kind: 'action.credit' }])).toBe(false);
  });

  it('Kamu SM production: the production server and sealing ready, with the causes', () => {
    const config = completeConfig();
    expect(stepState('kamusm_production', { config, tested: true }).done).toBe(true);
    config.sections.tsa.seal_ready = { ok: false, causes: ['roots', 'secret-store'] };
    expect(stepState('kamusm_production', { config, tested: true })).toEqual({
      done: false,
      missing: [
        { key: 'settings.kamusm.causes.roots', values: { defaultValue: 'roots' } },
        { key: 'settings.kamusm.causes.secret_store', values: { defaultValue: 'secret-store' } },
      ],
    });
    config.sections.tsa.seal_ready = { ok: false, causes: [] };
    expect(stepState('kamusm_production', { config, tested: true }).missing).toEqual([
      { key: 'setupWizard.missing.sealNotReady' },
    ]);
    config.sections.tsa.server = 'test';
    config.sections.tsa.seal_ready = { ok: true, causes: [] };
    expect(stepState('kamusm_production', { config, tested: true }).missing).toEqual([
      { key: 'setupWizard.missing.notProduction' },
    ]);
  });

  it('replica: switched on', () => {
    const config = completeConfig();
    expect(stepState('replica', { config, tested: false }).done).toBe(true);
    config.sections.replica.enabled = false;
    expect(stepState('replica', { config, tested: false }).done).toBe(false);
  });

  it('e-mail: an SMTP server and a sender, blank counts as missing', () => {
    const config = completeConfig();
    expect(stepState('smtp', { config, tested: false }).done).toBe(true);
    config.sections.smtp.from_address = '  ';
    expect(stepState('smtp', { config, tested: false }).missing).toEqual([
      { key: 'setupWizard.missing.smtpSender' },
    ]);
  });
});

describe('the first open step', () => {
  it('is the first step not done, and the summary when all are', () => {
    expect(firstOpenStep(wizardStates({ config: freshConfig(), tested: false }))).toBe('site');
    expect(firstOpenStep(wizardStates({ config: capturedConfig(), tested: false }))).toBe(
      'kamusm_test',
    );
    expect(firstOpenStep(wizardStates({ config: capturedConfig(), tested: true }))).toBe(
      'kamusm_production',
    );
    expect(firstOpenStep(wizardStates({ config: completeConfig(), tested: false }))).toBe(
      'summary',
    );
  });
});

describe('skip', () => {
  it('goes to the next step and keeps the skipped one open', () => {
    const input = { config: freshConfig(), tested: false };
    const before = wizardStates(input);
    expect(nextStep('site')).toBe('sources');
    expect(nextStep('smtp')).toBe('summary');
    expect(nextStep('summary')).toBe('summary');
    // Nothing is stored: the states are the same after a skip, and the wizard opens there again.
    expect(wizardStates(input)).toEqual(before);
    expect(wizardStates(input).site.done).toBe(false);
    expect(firstOpenStep(wizardStates(input))).toBe('site');
  });
});

describe('the sources step', () => {
  it('names the firewall note of each enabled source’s state', () => {
    const sources = capturedConfig().sections.sources;
    expect(firewallNotes(sources)).toEqual(['managed']);
    const [first] = sources;
    if (first) first.firewall = 'pending';
    expect(firewallNotes(sources)).toEqual(['managed']);
    for (const source of sources) source.firewall = 'not_managed';
    expect(firewallNotes(sources)).toEqual(['not_managed']);
    // A switched-off source needs no port.
    for (const source of sources) {
      source.enabled = false;
      source.firewall = 'closed';
    }
    expect(firewallNotes(sources)).toEqual([]);
    expect(firewallNotes([])).toEqual([]);
  });

  it('tells per configured source whether data arrived', () => {
    const sources = capturedConfig().sections.sources;
    const items: SourceHealth[] = SOURCES.overview.items.map((item) =>
      item.name === 'core-r1' ? { ...item, last_message: null } : item,
    );
    expect(arrivals(sources, items)).toEqual([
      {
        name: 'fortigate',
        enabled: true,
        arrived: true,
        lastMessage: SOURCES.overview.items.find((item) => item.name === 'fortigate')?.last_message,
      },
      { name: 'core-r1', enabled: true, arrived: false, lastMessage: null },
    ]);
    // Unknown senders and sources not configured are not listed; without the overview: unknown.
    expect(arrivals(sources, undefined).map((item) => item.arrived)).toEqual([null, null]);
  });
});
