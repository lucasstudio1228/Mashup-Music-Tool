import { useState } from 'react';
import AppShell from '../components/layout/AppShell';
import ProjectCard from '../components/projects/ProjectCard';
import ProjectFormModal from '../components/projects/ProjectFormModal';
import ConfirmDialog from '../components/shared/ConfirmDialog';
import {
  useCreateProject,
  useDeleteProject,
  useProjects,
} from '../api/projects';
import type { Project } from '../types';

export default function ProjectsPage() {
  const { data: projects, isLoading } = useProjects();
  const createProject = useCreateProject();
  const deleteProject = useDeleteProject();
  const [showForm, setShowForm] = useState(false);
  const [toDelete, setToDelete] = useState<Project | null>(null);

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
        onSubmit={(data) =>
          createProject.mutate(data, { onSuccess: () => setShowForm(false) })
        }
        onClose={() => setShowForm(false)}
      />

      <ConfirmDialog
        open={toDelete !== null}
        title="Delete project"
        message={`Delete "${toDelete?.name}" and all its output files? This cannot be undone.`}
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
