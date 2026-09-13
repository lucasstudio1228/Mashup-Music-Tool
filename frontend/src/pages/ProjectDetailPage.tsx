import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import AppShell from '../components/layout/AppShell';
import TrackList from '../components/tracks/TrackList';
import AddTracksPanel from '../components/tracks/AddTracksPanel';
import MixSettingsPanel from '../components/mixes/MixSettingsPanel';
import MixProgressModal from '../components/mixes/MixProgressModal';
import MixHistoryList from '../components/mixes/MixHistoryList';
import ProjectFormModal from '../components/projects/ProjectFormModal';
import ConfirmDialog from '../components/shared/ConfirmDialog';
import { StemPanel } from '../components/stems/StemPanel';
import { StemSectionHeader } from '../components/stems/StemSectionHeader';
import { ApplyAllPanel } from '../components/stems/ApplyAllPanel';
import { StemPlayerBar } from '../components/stems/StemPlayerBar';
import VideoPanel from '../components/video/VideoPanel';
import { useStemPlayer } from '../hooks/useStemPlayer';
import { useProject, useUpdateProject } from '../api/projects';
import { useTracks, useDeleteTrack } from '../api/tracks';
import { useMixes, useCreateMix, useDeleteMix } from '../api/mixes';
import type { Mix, MixCreate } from '../types';

type Tab = 'audio' | 'video';

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-white/5 bg-surface p-4">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-400">
        {title}
      </h2>
      {children}
    </div>
  );
}

export default function ProjectDetailPage() {
  const { id } = useParams();
  const projectId = Number(id);

  const { data: project, isLoading } = useProject(projectId);
  const { data: tracks } = useTracks(projectId);
  const { data: mixes } = useMixes(projectId);
  const updateProject = useUpdateProject(projectId);
  const deleteTrack = useDeleteTrack(projectId);
  const createMix = useCreateMix(projectId);
  const deleteMix = useDeleteMix(projectId);

  const [tab, setTab] = useState<Tab>('audio');
  const [editing, setEditing] = useState(false);
  const [activeMixId, setActiveMixId] = useState<number | null>(null);
  const [mixToDelete, setMixToDelete] = useState<Mix | null>(null);
  const [startError, setStartError] = useState<string | null>(null);

  const [playerState, playerControls] = useStemPlayer();

  function handlePlayTrack(trackId: number) {
    if (playerState.activeTrackId === trackId && playerState.isPlaying) {
      playerControls.pause();
    } else if (playerState.activeTrackId === trackId && !playerState.isPlaying) {
      playerControls.resume();
    } else {
      playerControls.play(trackId);
    }
  }

  if (isLoading || !project) {
    return (
      <AppShell title="Project">
        <p className="text-gray-400">Loading…</p>
      </AppShell>
    );
  }

  const trackList = tracks ?? [];
  const mixList = mixes ?? [];

  function startMix(settings: MixCreate) {
    setStartError(null);
    createMix.mutate(settings, {
      onSuccess: (mix) => setActiveMixId(mix.id),
      onError: (err: any) =>
        setStartError(err?.response?.data?.detail ?? 'Could not start mix'),
    });
  }

  const tabBtn = (t: Tab) =>
    `px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
      tab === t
        ? 'bg-accent text-white'
        : 'bg-surface-2 text-gray-300 hover:bg-white/10'
    }`;

  return (
    <AppShell
      title={project.name}
      actions={
        <>
          <Link
            to="/projects"
            className="rounded-lg px-3 py-2 text-sm text-gray-300 hover:bg-surface-2"
          >
            ← Back
          </Link>
          <button
            onClick={() => setEditing(true)}
            className="rounded-lg bg-surface-2 px-3 py-2 text-sm text-gray-200 hover:bg-white/10"
          >
            Edit
          </button>
        </>
      }
    >
      {/* Tabs: Audio / Video */}
      <div className="mb-5 flex gap-2">
        <button className={tabBtn('audio')} onClick={() => setTab('audio')}>
          🎵 Audio
        </button>
        <button className={tabBtn('video')} onClick={() => setTab('video')}>
          🎬 Video
        </button>
      </div>

      {tab === 'audio' && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          {/* Left column: Tracks */}
          <div className="space-y-4">
            <Section title={`Tracks (${trackList.length})`}>
              <TrackList tracks={trackList} onDelete={(tid) => deleteTrack.mutate(tid)} />
            </Section>
            <Section title="Add Tracks">
              <AddTracksPanel projectId={projectId} />
            </Section>

            {trackList.length > 0 && (
              <Section title="Stem Separation">
                <div className="space-y-2">
                  <StemSectionHeader projectId={projectId} />

                  <ApplyAllPanel projectId={projectId} />

                  <p className="text-xs text-gray-600">
                    ⚡ RTX 3060 · ~5–10 phút/track (chất lượng tối đa: shifts=5, overlap 0.5) · htdemucs_ft · -16 LUFS
                  </p>
                  <p className="text-xs text-gray-600">
                    🔄 Chạy nền — không ảnh hưởng render mix
                  </p>

                  <div className="space-y-1 max-h-[500px] overflow-y-auto
                                  pr-1 custom-scrollbar">
                    {trackList.map((track) => (
                      <StemPanel
                        key={track.id}
                        trackId={track.id}
                        trackName={track.filename}
                        isActiveTrack={playerState.activeTrackId === track.id}
                        isPlaying={playerState.isPlaying}
                        onPlayTrack={handlePlayTrack}
                        onSetVolume={playerControls.setVolume}
                      />
                    ))}
                  </div>
                </div>
              </Section>
            )}
          </div>

          {/* Right column: Mix settings + history */}
          <div className="space-y-4">
            <Section title="Mix Settings">
              <MixSettingsPanel
                trackCount={trackList.length}
                disabled={createMix.isPending}
                onStart={startMix}
              />
              {startError && (
                <p className="mt-2 text-xs text-red-400">{startError}</p>
              )}
            </Section>
            <Section title={`Mix History (${mixList.length})`}>
              <MixHistoryList mixes={mixList} onDelete={setMixToDelete} />
            </Section>
          </div>
        </div>
      )}

      {tab === 'video' && <VideoPanel projectId={projectId} />}

      {editing && (
        <ProjectFormModal
          open={editing}
          initial={project}
          saving={updateProject.isPending}
          onSubmit={(data) =>
            updateProject.mutate(data, { onSuccess: () => setEditing(false) })
          }
          onClose={() => setEditing(false)}
        />
      )}

      {activeMixId !== null && (
        <MixProgressModal
          mixId={activeMixId}
          projectId={projectId}
          onClose={() => setActiveMixId(null)}
        />
      )}

      <ConfirmDialog
        open={mixToDelete !== null}
        title="Delete mix"
        message={`Delete "${mixToDelete?.title}" and its output files?`}
        confirmLabel="Delete"
        danger
        onConfirm={() => {
          if (mixToDelete) deleteMix.mutate(mixToDelete.id);
          setMixToDelete(null);
        }}
        onCancel={() => setMixToDelete(null)}
      />

      {playerState.activeTrackId !== null && (
        <StemPlayerBar
          trackName={
            trackList.find(t => t.id === playerState.activeTrackId)?.filename ?? ""
          }
          isPlaying={playerState.isPlaying}
          isLoading={playerState.isLoading}
          currentTime={playerState.currentTime}
          duration={playerState.duration}
          error={playerState.error}
          onPlay={() => playerControls.play(playerState.activeTrackId!)}
          onPause={playerControls.pause}
          onResume={playerControls.resume}
          onStop={playerControls.stop}
          onSeek={playerControls.seek}
        />
      )}
    </AppShell>
  );
}
