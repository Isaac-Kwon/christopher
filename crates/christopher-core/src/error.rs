use std::fmt;

/// Errors raised for invalid configuration or input text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    /// Malformed input (letters, pairs, names).
    Parse(String),
    /// Well-formed but invalid machine configuration.
    Config(String),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Parse(m) => write!(f, "parse error: {m}"),
            Error::Config(m) => write!(f, "configuration error: {m}"),
        }
    }
}

impl std::error::Error for Error {}

pub type Result<T> = std::result::Result<T, Error>;
