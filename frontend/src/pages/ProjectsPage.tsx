import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import AppShell from '../components/layout/AppShell';
import BatchProgressBar from '../components/projects/BatchProgressBar';
import ProjectCard from '../components/projects/ProjectCard';
import ProjectFormModal from '../components/projects/ProjectFormModal';
import ConfirmDialog from '../components/shared/ConfirmDialog';
import {
  useActiveBatch,
  useCreateBatch,
  useCreateProject,
  useDeleteProject,
  useProjects,
} from '../api/projects';
import type { Project } from '../types';

// Lô chạy rất lâu (hàng giờ) → nhớ key qua reload để vẫn theo dõi được tiến độ.
const BATCH_KEY_STORAGE = 'activeBatchKey';

export default function ProjectsPage() {
  const { data: projects, isLoading } = useProjects();
  const createProject = useCreateProject();
  const navigate = useNavigate();
  const createBatch = useCreateBatch();
  const deleteProject = useDeleteProject();
  const [showForm, setShowForm] = useState(false);
  const [toDelete, setToDelete] = useState<Project | null>(null);
  const [batchKey, setBatchKey] = useState<string | null>(() =>
    localStorage.getItem(BATCH_KEY_STORAGE)
  );

  // Server đang chạy lô khác lô đang hiện (vd request tạo lô timeout nên chưa lưu
  // key) → tự chuyển thanh tiến độ sang lô đang chạy thật.
  const { data: active } = useActiveBatch();
  const activeKey = active?.batch_key ?? null;
  useEffect(() => {
    if (activeKey && activeKey !== batchKey) {
      localStorage.setItem(BATCH_KEY_STORAGE, activeKey);
      setBatchKey(activeKey);
    }
  }, [activeKey, batchKey]);

  function dismissBatch() {
    localStorage.removeItem(BATCH_KEY_STORAGE);
    setBatchKey(null);
  }

  const batchError =
    (createBatch.error as any)?.response?.data?.detail ??
    (createBatch.error as Error | null)?.message ??
    null;

  return (
    <AppShell
      title="Projects"
      actions={
        <button
          onClick={() => setShowForm(true)}
          className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover"
        >
          + New Project
        </button>
      }
    >
      {batchKey && (
        <BatchProgressBar batchKey={batchKey} onDismiss={dismissBatch} />
      )}
      {deleteProject.error && <p role="alert" className="mb-4 text-red-300">
        {(deleteProject.error as any)?.response?.data?.detail ?? deleteProject.error.message}
      </p>}
      {deleteProject.data?.warnings?.length > 0 && <div role="alert" className="mb-4 text-amber-300">
        Project đã xoá khỏi danh sách nhưng còn file chờ dọn:
        {deleteProject.data.warnings.map((warning: string) => <p key={warning}>{warning}</p>)}
      </div>}
      {isLoading ? (
        <p className="text-gray-400">Loading…</p>
      ) : !projects || projects.length === 0 ? (
        <div className="mt-20 text-center text-gray-400">
          <p className="mb-2 text-lg">No projects yet.</p>
          <p className="text-sm">Create your first one to get started.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {projects.map((p) => (
            <ProjectCard key={p.id} project={p} onDelete={setToDelete} />
          ))}
        </div>
      )}

      <ProjectFormModal
        open={showForm}
        saving={createProject.isPending}
        batchSaving={createBatch.isPending}
        batchError={batchError}
        onSubmit={(data, { autoRun }) =>
          createProject.mutate(data, {
            onSuccess: (p) => {
              setShowForm(false);
              // Tự bấm «✨ AI viết prompt & CHẠY TỰ ĐỘNG» ở tab Suno của project mới.
              if (autoRun) navigate(`/projects/${p.id}?tab=suno&autorun=1`);
            },
          })
        }
        onSubmitBatch={(data) =>
          createBatch.mutate(data, {
            onSuccess: (run) => {
              localStorage.setItem(BATCH_KEY_STORAGE, run.batch_key);
              setBatchKey(run.batch_key);
              setShowForm(false);
            },
          })
        }
        onClose={() => {
          createBatch.reset();
          setShowForm(false);
        }}
      />

      <ConfirmDialog
        open={toDelete !== null}
        title="Delete project"
        message={`Xoá "${toDelete?.name}" cùng ảnh, clip, video final, nhạc upload, output, stems và dữ liệu Suno thuộc project? Không thể hoàn tác sau khi dọn xong. File nhạc gốc được import từ thư mục ngoài vẫn được giữ.`}
        confirmLabel="Delete"
        danger
        onConfirm={() => {
          if (toDelete) deleteProject.mutate(toDelete.id);
          setToDelete(null);
        }}
        onCancel={() => setToDelete(null)}
      />
    </AppShell>
  );
}
