const j = (r) => { if (!r.ok) throw new Error(`${r.status}`); return r.json(); };

export const api = {
  sources: () => fetch("/api/sources").then(j),
  sourceDetail: (id, range = "3m") => fetch(`/api/sources/${id}?range=${range}`).then(j),
  movements: () => fetch("/api/movements").then(j),
  jobs: () => fetch("/api/jobs/latest").then(j),
  series: (instruments, { normalize = 0, range = "6m" } = {}) =>
    fetch(`/api/series?instruments=${instruments.join(",")}&normalize=${normalize}&range=${range}`).then(j),
};
