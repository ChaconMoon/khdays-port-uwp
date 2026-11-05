#include "khdays/vfs/filesystem.h"

#include <algorithm>
#include <fstream>
#include <stdexcept>
#include <string>
#include <system_error>
#include <unordered_map>
#include <vector>

namespace khdays::vfs {

namespace {

std::filesystem::path g_data_root{};
std::filesystem::path g_mods_root{"mods"};
std::vector<std::filesystem::path> g_cached_mods;
std::unordered_map<std::string, std::filesystem::path> g_cached_mod_files;
bool g_mods_scanned = false;

const std::vector<std::filesystem::path>& get_mods() {
    if (!g_mods_scanned) {
        g_mods_scanned = true;
        g_cached_mods.clear();
        g_cached_mod_files.clear();
        std::error_code ec;
        if (std::filesystem::is_directory(g_mods_root, ec)) {
            for (const auto& entry : std::filesystem::directory_iterator(g_mods_root, ec)) {
                if (entry.is_directory(ec)) {
                    g_cached_mods.push_back(entry.path());
                }
            }
        }
        if (g_mods_root != "mods" && std::filesystem::is_directory("mods", ec)) {
            for (const auto& entry : std::filesystem::directory_iterator("mods", ec)) {
                if (entry.is_directory(ec)) {
                    g_cached_mods.push_back(entry.path());
                }
            }
        }
        std::sort(g_cached_mods.begin(), g_cached_mods.end());

        // Pre-index any files/ subfolder in mods to eliminate synchronous disk queries during boot
        for (const auto& mod : g_cached_mods) {
            const auto files_dir = mod / "files";
            if (std::filesystem::is_directory(files_dir, ec)) {
                for (const auto& file_entry : std::filesystem::recursive_directory_iterator(files_dir, ec)) {
                    if (file_entry.is_regular_file(ec)) {
                        auto rel_p = std::filesystem::relative(file_entry.path(), files_dir, ec);
                        std::string key = rel_p.generic_string();
                        if (g_cached_mod_files.find(key) == g_cached_mod_files.end()) {
                            g_cached_mod_files.emplace(std::move(key), file_entry.path());
                        }
                    }
                }
            }
        }
    }
    return g_cached_mods;
}

// Normalize a game path to a relative path: strip leading slashes, unify
// separators, and reject any component that would escape the root.
std::optional<std::filesystem::path> normalize(std::string_view game_path) {
    std::string cleaned;
    cleaned.reserve(game_path.size());
    for (const char c : game_path) {
        cleaned.push_back(c == '\\' ? '/' : c);
    }
    std::size_t start = 0;
    while (start < cleaned.size() && cleaned[start] == '/') {
        ++start;
    }
    const std::filesystem::path rel{cleaned.substr(start)};
    // A root name ("C:", "C:/...") would make `root / rel` discard the root
    // entirely, so it escapes as surely as "..".
    if (rel.empty() || rel.has_root_name() || rel.has_root_directory()) {
        return std::nullopt;
    }
    for (const auto& part : rel) {
        if (part == "..") {
            return std::nullopt;  // no traversal outside the root
        }
    }
    return rel;
}

bool is_file(const std::filesystem::path& path) {
    std::error_code ec;
    return std::filesystem::is_regular_file(path, ec);
}

}  // namespace

std::optional<std::filesystem::path> find_in_mods(const std::filesystem::path& rel) {
    get_mods();
    const std::string gen = rel.generic_string();
    if (gen.rfind("files/", 0) == 0) {
        const auto it = g_cached_mod_files.find(gen.substr(6));
        if (it != g_cached_mod_files.end()) {
            return it->second;
        }
        return std::nullopt;
    }
    const auto& mods = get_mods();
    if (mods.empty()) {
        return std::nullopt;
    }
    for (const auto& mod : mods) {
        const auto candidate = mod / rel;
        if (is_file(candidate)) {
            return candidate;
        }
    }
    return std::nullopt;
}

namespace {

// Mod overrides: mods/<Mod>/files/<rel>, mods visited in sorted order.
std::optional<std::filesystem::path> find_mod_file(const std::filesystem::path& rel) {
    get_mods();
    const auto it = g_cached_mod_files.find(rel.generic_string());
    if (it != g_cached_mod_files.end()) {
        return it->second;
    }
    return std::nullopt;
}

}  // namespace

void set_data_root(const std::filesystem::path& root) {
    g_data_root = root;
}

const std::filesystem::path& data_root() {
    return g_data_root;
}

void set_mods_root(const std::filesystem::path& root) {
    g_mods_root = root;
    g_mods_scanned = false;
    g_cached_mod_files.clear();
}

bool autodetect_data_root(const std::filesystem::path& extracted_dir) {
    std::error_code ec;
    if (!std::filesystem::is_directory(extracted_dir, ec)) {
        return false;
    }
    for (const auto& entry : std::filesystem::directory_iterator(extracted_dir, ec)) {
        // an extraction still being made (the installer's, named <hash>.partial until it is whole)
        // is never taken
        if (entry.path().extension() == ".partial") {
            continue;
        }
        if (entry.is_directory(ec) && std::filesystem::is_directory(entry.path() / "nitrofs", ec)) {
            g_data_root = entry.path();
            return true;
        }
    }
    return false;
}

std::optional<std::filesystem::path> resolve(std::string_view game_path) {
    const auto rel = normalize(game_path);
    if (!rel) {
        return std::nullopt;
    }

    if (const auto mod = find_mod_file(*rel)) {
        return mod;
    }
    if (!g_data_root.empty()) {
        const std::filesystem::path views[] = {
            g_data_root / "decompressed" / *rel,
            g_data_root / "nitrofs" / *rel,
        };
        for (const auto& candidate : views) {
            if (is_file(candidate)) {
                return candidate;
            }
        }
    }
    return std::nullopt;
}

std::optional<std::filesystem::path> resolve_original(std::string_view game_path) {
    const auto rel = normalize(game_path);
    if (!rel) {
        return std::nullopt;
    }
    if (const auto mod = find_mod_file(*rel)) {
        return mod;
    }
    if (!g_data_root.empty()) {
        return g_data_root / "nitrofs" / *rel;
    }
    return std::nullopt;
}

bool exists(std::string_view game_path) {
    return resolve(game_path).has_value();
}

std::vector<std::uint8_t> read(std::string_view game_path) {
    const auto path = resolve(game_path);
    if (!path) {
        throw std::runtime_error("game file not found: " + std::string{game_path});
    }
    std::ifstream stream{*path, std::ios::binary};
    if (!stream) {
        throw std::runtime_error("cannot open game file: " + path->string());
    }
    stream.seekg(0, std::ios::end);
    const std::streamoff end = stream.tellg();
    std::vector<std::uint8_t> data(end > 0 ? static_cast<std::size_t>(end) : 0U);
    stream.seekg(0, std::ios::beg);
    if (!data.empty()) {
        stream.read(reinterpret_cast<char*>(data.data()), static_cast<std::streamsize>(data.size()));
    }
    return data;
}

}  // namespace khdays::vfs
