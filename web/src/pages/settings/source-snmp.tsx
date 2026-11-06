import { Plus, Trash2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { SwitchField, TextField } from '@/components/admin/form-fields';
import { SecretField } from '@/components/admin/secret-field';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import type { SnmpAuth, SnmpPriv } from '@/lib/settings-types';

import {
  NEW_LINE,
  SNMP_AUTHS,
  SNMP_LINES_MAX,
  SNMP_PRIVS,
  snmpId,
  type SnmpField,
  type SnmpForm,
} from './snmp-model';

/**
 * The SNMP part of a source (Wave 5 §6 N1, P4): switched on, ARCILOGGER reads the counters of
 * the device's WAN interfaces over SNMPv3 with authentication and privacy (no v1/v2c) and shows
 * the line's speed on the Panel and on Canlı Durum. The two keys are write-only, as SMTP's
 * password: the browser only learns whether each is stored. The device is tried with the new
 * values when the change is saved (N5); a refusal of the try comes back on the request.
 */
export function SnmpSection({
  values,
  onChange,
  message,
}: {
  values: SnmpForm;
  onChange: (values: SnmpForm) => void;
  /** The message at a field: the server's refusal, else the browser's check. */
  message: (field: SnmpField) => string | undefined;
}) {
  const { t } = useTranslation();
  const set = (next: Partial<SnmpForm>) => {
    onChange({ ...values, ...next });
  };
  const setLine = (index: number, next: Partial<SnmpForm['lines'][number]>) => {
    set({ lines: values.lines.map((line, at) => (at === index ? { ...line, ...next } : line)) });
  };
  const section = message('snmp');
  return (
    <fieldset
      className="flex flex-col gap-4 rounded-lg border border-border p-4"
      data-testid="source-snmp"
    >
      <legend className="px-1 text-13 font-semibold text-foreground">{t('snmp.form.title')}</legend>
      <SwitchField
        id={snmpId('snmp')}
        label={t('snmp.form.enabled')}
        hint={t('snmp.form.enabledHint')}
        checked={values.enabled}
        onChange={(enabled) => {
          set({ enabled });
        }}
      />
      {section ? (
        <Alert tone="danger" data-testid="source-snmp-error">
          {section}
        </Alert>
      ) : null}
      {values.enabled ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField
              id={snmpId('user')}
              label={t('snmp.form.user')}
              hint={t('snmp.form.userHint')}
              value={values.user}
              error={message('user')}
              mono
              spellCheck={false}
              autoComplete="off"
              required
              onChange={(user) => {
                set({ user });
              }}
            />
            <TextField
              id={snmpId('port')}
              label={t('snmp.form.port')}
              hint={t('snmp.form.portHint')}
              value={values.port}
              error={message('port')}
              inputMode="numeric"
              inputClassName="w-32"
              onChange={(port) => {
                set({ port });
              }}
            />
            <Field id="source-snmp-auth" label={t('snmp.form.auth')}>
              <Select
                value={values.auth}
                onValueChange={(auth) => {
                  set({ auth: auth as SnmpAuth });
                }}
              >
                <SelectTrigger id="source-snmp-auth">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SNMP_AUTHS.map((auth) => (
                    <SelectItem key={auth} value={auth}>
                      {t(`snmp.form.auths.${auth}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field id="source-snmp-priv" label={t('snmp.form.priv')} hint={t('snmp.form.privHint')}>
              <Select
                value={values.priv}
                onValueChange={(priv) => {
                  set({ priv: priv as SnmpPriv });
                }}
              >
                <SelectTrigger id="source-snmp-priv" aria-describedby="source-snmp-priv-hint">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SNMP_PRIVS.map((priv) => (
                    <SelectItem key={priv} value={priv}>
                      {t(`snmp.form.privs.${priv}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          </div>
          <SecretField
            id={snmpId('auth_key')}
            label={t('snmp.form.authKey')}
            meta={{ set: values.authKeySet, changed_at: null, changed_by: null }}
            edit={values.authKey}
            error={message('auth_key')}
            hint={t('snmp.form.keyHint')}
            onChange={(authKey) => {
              set({ authKey });
            }}
          />
          <SecretField
            id={snmpId('priv_key')}
            label={t('snmp.form.privKey')}
            meta={{ set: values.privKeySet, changed_at: null, changed_by: null }}
            edit={values.privKey}
            error={message('priv_key')}
            hint={t('snmp.form.keyHint')}
            onChange={(privKey) => {
              set({ privKey });
            }}
          />
          <div className="flex flex-col gap-2" role="group" aria-labelledby="source-snmp-lines">
            <h3 id="source-snmp-lines" className="text-13 font-medium text-foreground">
              {t('snmp.form.lines')}
            </h3>
            <p className="text-xs text-muted">{t('snmp.form.linesHint')}</p>
            <ul className="flex flex-col gap-3">
              {values.lines.map((line, index) => (
                // A line has no identity of its own but its place.
                <li
                  key={index}
                  className="grid items-start gap-2 sm:grid-cols-[1fr_7rem_7rem_auto]"
                >
                  <TextField
                    id={snmpId(`line-${index}`)}
                    label={t('snmp.form.line', { number: index + 1 })}
                    value={line.name}
                    error={message(`line-${index}`)}
                    mono
                    spellCheck={false}
                    autoComplete="off"
                    required={index === 0}
                    onChange={(name) => {
                      setLine(index, { name });
                    }}
                  />
                  <TextField
                    id={snmpId(`down-${index}`)}
                    label={t('snmp.form.down')}
                    value={line.down}
                    error={message(`down-${index}`)}
                    inputMode="numeric"
                    onChange={(down) => {
                      setLine(index, { down });
                    }}
                  />
                  <TextField
                    id={snmpId(`up-${index}`)}
                    label={t('snmp.form.up')}
                    value={line.up}
                    error={message(`up-${index}`)}
                    inputMode="numeric"
                    onChange={(up) => {
                      setLine(index, { up });
                    }}
                  />
                  {values.lines.length > 1 ? (
                    <Button
                      size="icon-sm"
                      variant="ghost"
                      className="sm:mt-7"
                      aria-label={t('snmp.form.removeLine', { number: index + 1 })}
                      onClick={() => {
                        set({ lines: values.lines.filter((_, at) => at !== index) });
                      }}
                    >
                      <Trash2 aria-hidden="true" />
                    </Button>
                  ) : null}
                </li>
              ))}
            </ul>
            <p className="text-xs text-muted">{t('snmp.form.capacityHint')}</p>
            {values.lines.length < SNMP_LINES_MAX ? (
              <Button
                size="sm"
                variant="secondary"
                className="self-start"
                onClick={() => {
                  set({ lines: [...values.lines, NEW_LINE] });
                }}
              >
                <Plus aria-hidden="true" />
                {t('snmp.form.addLine')}
              </Button>
            ) : null}
          </div>
          <TextField
            id={snmpId('interval')}
            label={t('snmp.form.interval')}
            hint={t('snmp.form.intervalHint')}
            value={values.interval}
            error={message('interval')}
            inputMode="numeric"
            inputClassName="w-32"
            onChange={(interval) => {
              set({ interval });
            }}
          />
          <Alert tone="info" assertive={false} data-testid="source-snmp-probe">
            {t('snmp.form.probeNote')}
          </Alert>
        </>
      ) : null}
    </fieldset>
  );
}
