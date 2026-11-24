use pyo3::prelude::*;

fn entries(blocks: &[(String, String)]) -> Vec<(&str, &str)> { blocks.iter().map(|(key, text)| (key.as_str(), text.as_str())).collect() }

#[pyclass(module = "teleprint")]
struct Transcript(teleprint::Transcript);

#[pymethods]
impl Transcript {
    #[new]
    fn new(cols: u16, rows: u16) -> Self { Self(teleprint::Transcript::new(cols, rows)) }

    fn put(&mut self, key: &str, text: &str) -> String { self.0.put(key, text) }
    #[pyo3(signature = (blocks, tail="", cursor=None))]
    fn sync(&mut self, blocks: Vec<(String, String)>, tail: &str, cursor: Option<usize>) -> String { self.0.sync(&entries(&blocks), tail, cursor) }
    #[pyo3(signature = (cols, rows, blocks, tail="", cursor=None))]
    fn sync_at(&mut self, cols: u16, rows: u16, blocks: Vec<(String, String)>, tail: &str, cursor: Option<usize>) -> String {
        self.0.sync_at(cols, rows, &entries(&blocks), tail, cursor)
    }
    #[pyo3(signature = (cols, rows, blocks, tail="", cursor=None))]
    fn sync_projected_at(&mut self, cols: u16, rows: u16, blocks: Vec<(String, String, String)>, tail: &str, cursor: Option<usize>) -> String {
        self.0.sync_projected_at(cols, rows, blocks.iter().map(|(k,s,t)| (k.as_str(),s.as_str(),t.as_str())), tail, cursor)
    }
    fn reset(&mut self) { self.0.reset() }
    fn resize(&mut self, cols: u16, rows: u16) -> String { self.0.resize(cols, rows) }
    fn finish(&mut self) -> String { self.0.finish() }
}

#[pyclass(module = "teleprint")]
struct Image(teleprint::kitty::Image);

#[pymethods]
impl Image {
    #[staticmethod]
    fn decode(data: &str) -> Option<Self> { teleprint::kitty::Image::decode(data).map(Self) }
    fn size(&self) -> (u32, u32) { self.0.size() }
    #[pyo3(signature = (id, max_cols, tmux=false))]
    fn render(&self, id: u32, max_cols: u16, tmux: bool) -> (String, String) { self.0.render(id, max_cols, tmux) }
}

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_class::<Image>()?;
    m.add_class::<Transcript>()
}
