import { zodResolver } from '@hookform/resolvers/zod';
import { AlertCircle, ArrowUpRight, Book, Download, Loader2, RefreshCw } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useForm } from 'react-hook-form';
import { Trans, useTranslation } from 'react-i18next';
import * as z from 'zod';
import { Button } from '@/components/ui/button';
import { Form, FormControl, FormField, FormItem, FormMessage } from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Progress } from '@/components/ui/progress';
import { Toggle } from '@/components/ui/toggle';
import { useToast } from '@/components/ui/use-toast';
import { useAutoUpdater } from '@/hooks/useAutoUpdater';
import { useServerHealth } from '@/lib/hooks/useServer';
import { usePlatform } from '@/platform/PlatformContext';
import { useServerStore } from '@/stores/serverStore';
import { CloudSection } from './CloudSection';
import { LanguageSelect } from './LanguageSelect';
import { SettingRow, SettingSection } from './SettingRow';
import { ThemeSelect } from './ThemeSelect';

function makeConnectionSchema(invalidUrl: string) {
  return z.object({
    serverUrl: z.string().url(invalidUrl),
  });
}

type ConnectionFormValues = { serverUrl: string };

export function GeneralPage() {
  const { t } = useTranslation();
  const platform = usePlatform();
  const serverUrl = useServerStore((state) => state.serverUrl);
  const setServerUrl = useServerStore((state) => state.setServerUrl);
  const keepServerRunningOnClose = useServerStore((state) => state.keepServerRunningOnClose);
  const setKeepServerRunningOnClose = useServerStore((state) => state.setKeepServerRunningOnClose);
  const mode = useServerStore((state) => state.mode);
  const setMode = useServerStore((state) => state.setMode);
  const workerProcesses = useServerStore((state) => state.workerProcesses);
  const setWorkerProcesses = useServerStore((state) => state.setWorkerProcesses);
  const { toast } = useToast();
  const { data: health, isLoading, error: healthError } = useServerHealth();

  const resolver = useMemo(
    () => zodResolver(makeConnectionSchema(t('settings.general.serverUrl.invalidUrl'))),
    [t],
  );
  const form = useForm<ConnectionFormValues>({
    resolver,
    defaultValues: { serverUrl },
  });

  useEffect(() => {
    form.reset({ serverUrl });
  }, [serverUrl, form]);

  // Re-run validation when the locale changes so existing error messages retranslate.
  useEffect(() => {
    if (form.formState.errors.serverUrl) {
      form.trigger('serverUrl');
    }
  }, [t, form]);

  const { isDirty } = form.formState;

  function onSubmit(data: ConnectionFormValues) {
    setServerUrl(data.serverUrl);
    form.reset(data);
    toast({
      title: t('settings.general.serverUrl.updatedTitle'),
      description: t('settings.general.serverUrl.updatedDescription', { url: data.serverUrl }),
    });
  }

  return (
    <div className="space-y-8 max-w-2xl">
      <div className="grid gap-3">
        <a
          href="https://github.com/koval-yurko/voicebox/wiki"
          target="_blank"
          rel="noopener noreferrer"
          className="group flex items-center gap-3 rounded-lg border border-border/60 p-4 transition-colors hover:bg-muted/50"
        >
          <Book className="h-5 w-5 shrink-0 text-accent" strokeWidth={2.5} />
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium">{t('settings.general.docs.title')}</div>
            <div className="text-xs text-muted-foreground">GitHub Wiki</div>
          </div>
          <ArrowUpRight className="h-4 w-4 text-muted-foreground/40 group-hover:text-muted-foreground transition-colors" />
        </a>
      </div>

      <SettingSection>
        <SettingRow
          title={t('settings.general.serverUrl.title')}
          description={t('settings.general.serverUrl.description')}
          action={
            <ConnectionStatus health={health} isLoading={isLoading} healthError={healthError} />
          }
        >
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="flex gap-2">
              <FormField
                control={form.control}
                name="serverUrl"
                render={({ field }) => (
                  <FormItem className="flex-1">
                    <FormControl>
                      <Input placeholder="http://127.0.0.1:17593" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              {isDirty && (
                <Button type="submit" size="sm">
                  {t('common.save')}
                </Button>
              )}
            </form>
          </Form>
        </SettingRow>

        <SettingRow
          title={t('settings.general.keepServerRunning.title')}
          description={t('settings.general.keepServerRunning.description')}
          htmlFor="keepServerRunning"
          action={
            <Toggle
              id="keepServerRunning"
              checked={keepServerRunningOnClose}
              onCheckedChange={(checked: boolean) => {
                setKeepServerRunningOnClose(checked);
                platform.lifecycle.setKeepServerRunning(checked).catch((error) => {
                  console.error('Failed to sync setting to Rust:', error);
                  setKeepServerRunningOnClose(!checked);
                  toast({
                    title: t('settings.general.keepServerRunning.failedTitle'),
                    description: t('settings.general.keepServerRunning.failedDescription'),
                    variant: 'destructive',
                  });
                  return;
                });
                toast({
                  title: t('settings.general.keepServerRunning.updatedTitle'),
                  description: checked
                    ? t('settings.general.keepServerRunning.runningDescription')
                    : t('settings.general.keepServerRunning.stoppedDescription'),
                });
              }}
            />
          }
        />

        {platform.metadata.isTauri && (
          <SettingRow
            title={t('settings.general.workerProcesses.title')}
            description={t('settings.general.workerProcesses.description')}
            htmlFor="workerProcesses"
            action={
              <Toggle
                id="workerProcesses"
                checked={workerProcesses}
                onCheckedChange={(checked: boolean) => {
                  setWorkerProcesses(checked);
                  toast({
                    title: t('settings.general.workerProcesses.updatedTitle'),
                    description: t('settings.general.workerProcesses.restartDescription'),
                  });
                }}
              />
            }
          />
        )}

        {platform.metadata.isTauri && (
          <SettingRow
            title={t('settings.general.networkAccess.title')}
            description={t('settings.general.networkAccess.description')}
            htmlFor="allowNetworkAccess"
            action={
              <Toggle
                id="allowNetworkAccess"
                checked={mode === 'remote'}
                onCheckedChange={(checked: boolean) => {
                  setMode(checked ? 'remote' : 'local');
                  toast({
                    title: t('settings.general.networkAccess.updatedTitle'),
                    description: checked
                      ? t('settings.general.networkAccess.enabled')
                      : t('settings.general.networkAccess.disabled'),
                  });
                }}
              />
            }
          />
        )}

        <SettingRow
          title={t('settings.language.label')}
          description={t('settings.language.description')}
          action={<LanguageSelect />}
        />

        <SettingRow
          title={t('settings.theme.label')}
          description={t('settings.theme.description')}
          action={<ThemeSelect />}
        />
      </SettingSection>

      {health?.cloud_enabled && <CloudSection />}

      <ApiReferenceCard serverUrl={serverUrl} />
      {health?.tts_url && <WorkerApiCard worker="tts" url={health.tts_url} />}
      {health?.stt_url && <WorkerApiCard worker="stt" url={health.stt_url} />}

      {platform.metadata.isTauri && <UpdatesSection />}
    </div>
  );
}

function ConnectionStatus({
  health,
  isLoading,
  healthError,
}: {
  health: ReturnType<typeof useServerHealth>['data'];
  isLoading: boolean;
  healthError: ReturnType<typeof useServerHealth>['error'];
}) {
  const { t } = useTranslation();
  if (isLoading) {
    return (
      <div className="flex items-center gap-2 rounded-full border border-border/60 px-3 py-1">
        <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
        <span className="text-xs text-muted-foreground">
          {t('settings.general.connection.connecting')}
        </span>
      </div>
    );
  }
  if (healthError) {
    return (
      <div className="flex items-center gap-2 rounded-full border border-destructive/30 px-3 py-1">
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full rounded-full bg-destructive/40" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-destructive" />
        </span>
        <span className="text-xs text-destructive">{t('settings.general.connection.offline')}</span>
      </div>
    );
  }
  if (health) {
    return (
      <div className="flex items-center gap-2 rounded-full border border-accent/30 px-3 py-1">
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent/60" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-accent shadow-[0_0_6px_1px_hsl(var(--accent)/0.5)]" />
        </span>
        <span className="text-xs text-muted-foreground">
          {t('settings.general.connection.online')}
        </span>
      </div>
    );
  }
  return null;
}

function UpdatesSection() {
  const { t } = useTranslation();
  const platform = usePlatform();
  const { status, checkForUpdates, downloadAndInstall, restartAndInstall } = useAutoUpdater(false);
  const [currentVersion, setCurrentVersion] = useState<string | null>('');
  const isDev = !import.meta.env?.PROD;

  useEffect(() => {
    platform.metadata
      .getVersion()
      .then(setCurrentVersion)
      .catch(() => setCurrentVersion(null));
  }, [platform]);

  const versionLabel = currentVersion ?? t('common.unknown');

  return (
    <SettingSection
      title={t('settings.general.updates.title')}
      description={`v${versionLabel}${isDev ? t('settings.general.updates.devSuffix') : ''}`}
    >
      {isDev ? (
        <SettingRow
          title={t('settings.general.updates.devMode.title')}
          description={t('settings.general.updates.devMode.description')}
        />
      ) : (
        <>
          <SettingRow
            title={t('settings.general.updates.check.title')}
            description={
              status.available
                ? t('settings.general.updates.check.available', { version: status.version })
                : status.checking
                  ? t('settings.general.updates.check.checking')
                  : t('settings.general.updates.check.upToDate')
            }
            action={
              <Button
                onClick={checkForUpdates}
                disabled={status.checking || status.downloading || status.readyToInstall}
                variant="outline"
                size="sm"
              >
                <RefreshCw
                  className={`h-3.5 w-3.5 mr-1.5 ${status.checking ? 'animate-spin' : ''}`}
                />
                {t('settings.general.updates.check.button')}
              </Button>
            }
          />

          {status.error && (
            <SettingRow title={t('settings.general.updates.error')}>
              <div className="flex items-center gap-2 text-sm text-destructive">
                <AlertCircle className="h-4 w-4" />
                {status.error}
              </div>
            </SettingRow>
          )}

          {status.available && !status.downloading && !status.readyToInstall && (
            <SettingRow
              title={t('settings.general.updates.download.title', { version: status.version })}
              description={t('settings.general.updates.download.description')}
              action={
                <Button onClick={downloadAndInstall} size="sm">
                  <Download className="h-3.5 w-3.5 mr-1.5" />
                  {t('settings.general.updates.download.button')}
                </Button>
              }
            />
          )}

          {status.downloading && (
            <SettingRow title={t('settings.general.updates.downloading')}>
              <div className="space-y-1.5">
                <Progress value={status.downloadProgress} />
                <div className="flex items-center justify-between text-xs text-muted-foreground">
                  {status.downloadedBytes !== undefined &&
                  status.totalBytes !== undefined &&
                  status.totalBytes > 0 ? (
                    <span>
                      {(status.downloadedBytes / 1024 / 1024).toFixed(1)} MB /{' '}
                      {(status.totalBytes / 1024 / 1024).toFixed(1)} MB
                    </span>
                  ) : (
                    <span />
                  )}
                  {status.downloadProgress !== undefined && <span>{status.downloadProgress}%</span>}
                </div>
              </div>
            </SettingRow>
          )}

          {status.readyToInstall && (
            <SettingRow
              title={t('settings.general.updates.ready.title')}
              description={t('settings.general.updates.ready.description', {
                version: status.version,
              })}
              action={
                <Button onClick={restartAndInstall} size="sm">
                  <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
                  {t('settings.general.updates.ready.button')}
                </Button>
              }
            />
          )}
        </>
      )}
    </SettingSection>
  );
}

type Endpoint = { method: 'GET' | 'POST' | 'WS'; path: string; label: string };

function EndpointList({ endpoints }: { endpoints: Endpoint[] }) {
  return (
    <div className="space-y-1">
      {endpoints.map((ep) => (
        <div key={ep.path} className="flex items-center gap-2.5 py-1">
          <span
            className={`text-[10px] font-mono font-semibold w-9 text-center rounded px-1 py-px ${
              ep.method === 'GET' ? 'bg-muted text-muted-foreground' : 'bg-accent/10 text-accent'
            }`}
          >
            {ep.method}
          </span>
          <code className="text-xs font-mono text-muted-foreground">{ep.path}</code>
          <span className="text-xs text-muted-foreground/50 ml-auto">{ep.label}</span>
        </div>
      ))}
    </div>
  );
}

// Streaming routes run only on the workers; main refuses them, so clients call these URLs.
function WorkerApiCard({ worker, url }: { worker: 'tts' | 'stt'; url: string }) {
  const { t } = useTranslation();
  const endpoints: Endpoint[] =
    worker === 'tts'
      ? [
          {
            method: 'WS',
            path: '/tts/ws',
            label: t('settings.general.api.workers.endpoints.ttsWs'),
          },
          {
            method: 'POST',
            path: '/tts/stream',
            label: t('settings.general.api.workers.endpoints.ttsStream'),
          },
          {
            method: 'POST',
            path: '/tts/render',
            label: t('settings.general.api.workers.endpoints.ttsRender'),
          },
          {
            method: 'POST',
            path: '/tts/warmup',
            label: t('settings.general.api.workers.endpoints.ttsWarmup'),
          },
          { method: 'GET', path: '/health', label: t('settings.general.api.endpoints.health') },
        ]
      : [
          {
            method: 'WS',
            path: '/stt/ws',
            label: t('settings.general.api.workers.endpoints.sttWs'),
          },
          {
            method: 'POST',
            path: '/transcribe',
            label: t('settings.general.api.workers.endpoints.transcribe'),
          },
          { method: 'GET', path: '/health', label: t('settings.general.api.endpoints.health') },
        ];

  return (
    <div className="rounded-lg border border-border/60 p-4 space-y-3">
      <div>
        <h3 className="text-sm font-medium">{t(`settings.general.api.workers.${worker}Title`)}</h3>
        <p className="text-sm text-muted-foreground">
          <Trans
            i18nKey="settings.general.api.workers.description"
            values={{ url }}
            components={{
              code: <code className="text-xs bg-muted px-1 py-0.5 rounded font-mono" />,
            }}
          />
        </p>
      </div>
      <EndpointList endpoints={endpoints} />
    </div>
  );
}

function ApiReferenceCard({ serverUrl }: { serverUrl: string }) {
  const { t } = useTranslation();
  const endpoints: Endpoint[] = [
    { method: 'POST', path: '/generate', label: t('settings.general.api.endpoints.generate') },
    { method: 'GET', path: '/health', label: t('settings.general.api.endpoints.health') },
    { method: 'GET', path: '/profiles', label: t('settings.general.api.endpoints.profiles') },
    { method: 'GET', path: '/history', label: t('settings.general.api.endpoints.history') },
  ];

  return (
    <div className="rounded-lg border border-border/60 p-4 space-y-3">
      <div>
        <h3 className="text-sm font-medium">{t('settings.general.api.title')}</h3>
        <p className="text-sm text-muted-foreground">
          <Trans
            i18nKey="settings.general.api.description"
            values={{ url: serverUrl }}
            components={{
              code: <code className="text-xs bg-muted px-1 py-0.5 rounded font-mono" />,
            }}
          />
        </p>
      </div>
      <EndpointList endpoints={endpoints} />
      <p className="text-xs text-muted-foreground">
        <a
          href={`${serverUrl}/docs`}
          target="_blank"
          rel="noopener noreferrer"
          className="text-accent hover:underline"
        >
          {t('settings.general.api.viewReference')}
        </a>
      </p>
    </div>
  );
}
