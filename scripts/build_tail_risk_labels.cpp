#include <algorithm>
#include <array>
#include <cmath>
#include <deque>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <set>
#include <string>
#include <unordered_map>
#include <vector>

namespace {
constexpr int kWindow = 252;
constexpr double kP10 = 0.10;
constexpr double kP05 = 0.05;

struct Factor {
  double rmrf, smb, hml;
};

struct Row {
  std::string stock;
  std::string date;
  int day = -1;
  double ret = NAN, rf = NAN;
  std::array<double, 4> x{};
  bool valid_return = false;
  std::array<double, 4> beta{};
  bool beta_valid = false;
  double target = NAN;
  bool target_valid = false;
};

struct HistoricalFeatures {
  double return_1d = NAN;
  double cumulative_return_5d = NAN;
  double cumulative_return_20d = NAN;
  double volatility_20d = NAN;
  double volatility_60d = NAN;
  double downside_volatility_60d = NAN;
  int valid_returns_20d = 0;
  int valid_returns_60d = 0;
};

std::vector<std::string> split_csv(const std::string& line) {
  std::vector<std::string> fields;
  std::string field;
  bool quoted = false;
  for (size_t i = 0; i < line.size(); ++i) {
    char c = line[i];
    if (c == '"') {
      if (quoted && i + 1 < line.size() && line[i + 1] == '"') {
        field.push_back('"');
        ++i;
      } else {
        quoted = !quoted;
      }
    } else if (c == ',' && !quoted) {
      fields.push_back(field);
      field.clear();
    } else if (c != '\r') {
      field.push_back(c);
    }
  }
  fields.push_back(field);
  return fields;
}

double number(const std::string& value) {
  if (value.empty()) return NAN;
  try {
    return std::stod(value);
  } catch (...) {
    return NAN;
  }
}

double trailing_compound_return(const std::vector<Row>& rows, size_t end, int window) {
  double growth = 1.0;
  int expected_day = rows[end].day;
  size_t cursor = end;
  for (int k = 0; k < window; ++k, --expected_day) {
    while (true) {
      if (cursor >= rows.size() || rows[cursor].day < expected_day) return NAN;
      if (rows[cursor].day == expected_day) break;
      if (cursor == 0) return NAN;
      --cursor;
    }
    if (!rows[cursor].valid_return) return NAN;
    growth *= (1.0 + rows[cursor].ret);
    if (cursor == 0 && k + 1 < window) return NAN;
    if (cursor > 0) --cursor;
  }
  return growth - 1.0;
}

double sample_volatility(double sum, double sum_sq, int count) {
  if (count < 2) return NAN;
  const double centered = std::max(0.0, sum_sq - sum * sum / count);
  return std::sqrt(centered / (count - 1));
}

HistoricalFeatures historical_features(const std::vector<Row>& rows, size_t i) {
  HistoricalFeatures result;
  const Row& current = rows[i];
  if (current.valid_return) result.return_1d = current.ret;
  result.cumulative_return_5d = trailing_compound_return(rows, i, 5);
  result.cumulative_return_20d = trailing_compound_return(rows, i, 20);

  double sum20 = 0.0, sum_sq20 = 0.0;
  double sum60 = 0.0, sum_sq60 = 0.0, downside_sq60 = 0.0;
  const int start20 = current.day - 19;
  const int start60 = current.day - 59;
  for (size_t j = i + 1; j-- > 0;) {
    const Row& row = rows[j];
    if (row.day < start60) break;
    if (!row.valid_return) continue;
    const double excess = row.ret - row.rf;
    ++result.valid_returns_60d;
    sum60 += excess;
    sum_sq60 += excess * excess;
    const double downside = std::min(excess, 0.0);
    downside_sq60 += downside * downside;
    if (row.day >= start20) {
      ++result.valid_returns_20d;
      sum20 += excess;
      sum_sq20 += excess * excess;
    }
  }
  if (result.valid_returns_20d >= 16)
    result.volatility_20d = sample_volatility(sum20, sum_sq20, result.valid_returns_20d);
  if (result.valid_returns_60d >= 48) {
    result.volatility_60d = sample_volatility(sum60, sum_sq60, result.valid_returns_60d);
    result.downside_volatility_60d = std::sqrt(downside_sq60 / result.valid_returns_60d);
  }
  return result;
}

void write_number(std::ofstream& out, double value) {
  if (std::isfinite(value)) out << std::setprecision(12) << value;
}

bool solve4(const std::array<std::array<double, 4>, 4>& matrix,
            const std::array<double, 4>& rhs,
            std::array<double, 4>& answer) {
  double a[4][5];
  for (int i = 0; i < 4; ++i) {
    for (int j = 0; j < 4; ++j) a[i][j] = matrix[i][j];
    a[i][4] = rhs[i];
  }
  for (int col = 0; col < 4; ++col) {
    int pivot = col;
    for (int row = col + 1; row < 4; ++row)
      if (std::abs(a[row][col]) > std::abs(a[pivot][col])) pivot = row;
    if (std::abs(a[pivot][col]) < 1e-12) return false;
    for (int j = col; j < 5; ++j) std::swap(a[col][j], a[pivot][j]);
    const double scale = a[col][col];
    for (int j = col; j < 5; ++j) a[col][j] /= scale;
    for (int row = 0; row < 4; ++row) {
      if (row == col) continue;
      const double multiple = a[row][col];
      for (int j = col; j < 5; ++j) a[row][j] -= multiple * a[col][j];
    }
  }
  for (int i = 0; i < 4; ++i) answer[i] = a[i][4];
  return true;
}

class QuantileWindow {
 public:
  explicit QuantileWindow(double p) : p_(p) {}

  void insert(double value) {
    if (low_.empty() || value <= *low_.rbegin()) low_.insert(value);
    else high_.insert(value);
    rebalance();
  }

  void erase(double value) {
    auto low_it = low_.find(value);
    if (low_it != low_.end()) low_.erase(low_it);
    else {
      auto high_it = high_.find(value);
      if (high_it != high_.end()) high_.erase(high_it);
    }
    rebalance();
  }

  size_t size() const { return low_.size() + high_.size(); }
  double value() const { return *low_.rbegin(); }

 private:
  void rebalance() {
    const size_t n = low_.size() + high_.size();
    const size_t wanted = n ? static_cast<size_t>(std::floor(p_ * (n - 1))) + 1 : 0;
    while (low_.size() > wanted) {
      auto it = std::prev(low_.end());
      high_.insert(*it);
      low_.erase(it);
    }
    while (low_.size() < wanted) {
      auto it = high_.begin();
      low_.insert(*it);
      high_.erase(it);
    }
    if (!low_.empty() && !high_.empty() && *low_.rbegin() > *high_.begin()) {
      auto low_it = std::prev(low_.end());
      auto high_it = high_.begin();
      double low_value = *low_it, high_value = *high_it;
      low_.erase(low_it);
      high_.erase(high_it);
      low_.insert(high_value);
      high_.insert(low_value);
    }
  }

  double p_;
  std::multiset<double> low_, high_;
};

void write_group(const std::vector<Row>& rows, std::ofstream& out,
                 unsigned long long& label_rows,
                 unsigned long long& target_rows) {
  if (rows.empty()) return;
  std::deque<std::pair<std::array<double, 4>, double>> observations;
  std::array<std::array<double, 4>, 4> xx{};
  std::array<double, 4> xy{};
  std::vector<Row> work = rows;

  for (auto& row : work) {
    if (row.valid_return) {
      observations.emplace_back(row.x, row.ret - row.rf);
      for (int a = 0; a < 4; ++a) {
        xy[a] += row.x[a] * (row.ret - row.rf);
        for (int b = 0; b < 4; ++b) xx[a][b] += row.x[a] * row.x[b];
      }
      if (observations.size() > kWindow) {
        const auto old = observations.front();
        observations.pop_front();
        for (int a = 0; a < 4; ++a) {
          xy[a] -= old.first[a] * old.second;
          for (int b = 0; b < 4; ++b) xx[a][b] -= old.first[a] * old.first[b];
        }
      }
    }
    if (observations.size() == kWindow) {
      row.beta_valid = solve4(xx, xy, row.beta);
    }
  }

  for (size_t i = 0; i < work.size(); ++i) {
    if (!work[i].beta_valid || i + 5 >= work.size()) continue;
    double cumulative_residual = 0.0;
    bool complete = true;
    for (int k = 1; k <= 5; ++k) {
      const Row& future = work[i + k];
      if (future.day != work[i].day + k || !future.valid_return) {
        complete = false;
        break;
      }
      double fitted = 0.0;
      for (int j = 0; j < 4; ++j) fitted += work[i].beta[j] * future.x[j];
      cumulative_residual += (future.ret - future.rf) - fitted;
    }
    if (complete) {
      work[i].target = cumulative_residual;
      work[i].target_valid = true;
      ++target_rows;
    }
  }

  QuantileWindow q10(kP10), q05(kP05);
  std::deque<double> completed;
  size_t completion_cursor = 0;
  for (size_t i = 0; i < work.size(); ++i) {
    while (completion_cursor < i &&
           work[completion_cursor].day + 5 <= work[i].day) {
      const Row& prior = work[completion_cursor];
      if (prior.target_valid) {
        completed.push_back(prior.target);
        q10.insert(prior.target);
        q05.insert(prior.target);
        if (completed.size() > kWindow) {
          const double oldest = completed.front();
          completed.pop_front();
          q10.erase(oldest);
          q05.erase(oldest);
        }
      }
      ++completion_cursor;
    }
    if (!work[i].target_valid || completed.size() < kWindow) continue;
    const double t10 = q10.value(), t05 = q05.value();
    const HistoricalFeatures features = historical_features(work, i);
    out << work[i].stock << ',' << work[i].date << ',';
    write_number(out, features.return_1d);
    out << ',';
    write_number(out, features.cumulative_return_5d);
    out << ',';
    write_number(out, features.cumulative_return_20d);
    out << ',';
    write_number(out, features.volatility_20d);
    out << ',';
    write_number(out, features.volatility_60d);
    out << ',';
    write_number(out, features.downside_volatility_60d);
    out << ',' << features.valid_returns_20d << ',' << features.valid_returns_60d << ',';
    write_number(out, work[i].x[1]);
    out << ',';
    write_number(out, work[i].x[2]);
    out << ',';
    write_number(out, work[i].x[3]);
    out << ',';
    write_number(out, work[i].target);
    out << ',';
    write_number(out, t10);
    out << ',' << (work[i].target < t10 ? 1 : 0) << ',';
    write_number(out, t05);
    out << ',' << (work[i].target < t05 ? 1 : 0) << ',' << kWindow << '\n';
    ++label_rows;
  }
}

}  // namespace

int main(int argc, char** argv) {
  if (argc != 4) {
    std::cerr << "usage: build_tail_risk_labels SORTED_DRET FACTORS OUTPUT\n";
    return 2;
  }
  std::unordered_map<std::string, Factor> factors;
  std::vector<std::string> days;
  std::unordered_map<std::string, int> day_index;
  {
    std::ifstream factor_file(argv[2]);
    std::string line;
    while (std::getline(factor_file, line)) {
      auto f = split_csv(line);
      if (f.size() < 4) continue;
      const int idx = static_cast<int>(days.size());
      days.push_back(f[0]);
      day_index[f[0]] = idx;
      factors[f[0]] = Factor{number(f[1]), number(f[2]), number(f[3])};
    }
  }
  if (days.empty()) {
    std::cerr << "no selected factor rows\n";
    return 3;
  }

  std::ifstream input(argv[1]);
  std::ofstream output(argv[3]);
  if (!input || !output) {
    std::cerr << "cannot open input/output\n";
    return 4;
  }
  output << "stock_code,date,return_1d,cumulative_return_5d,cumulative_return_20d,"
            "volatility_20d,volatility_60d,downside_volatility_60d,valid_returns_20d,"
            "valid_returns_60d,rmrf_float,smb_float,hml_float,forward_5d_residual,"
            "threshold_p10,label_p10,threshold_p05,label_p05,regression_window\n";

  std::vector<Row> group;
  std::string current_stock, line;
  unsigned long long input_lines = 0, input_rows = 0, malformed_rows = 0;
  unsigned long long unmapped_rows = 0, labels = 0, targets = 0;
  while (std::getline(input, line)) {
    ++input_lines;
    auto f = split_csv(line);
    if (f.size() < 11) {
      ++malformed_rows;
      continue;
    }
    const std::string stock = f[1];
    if (!current_stock.empty() && stock != current_stock) {
      write_group(group, output, labels, targets);
      group.clear();
    }
    current_stock = stock;
    const auto day_it = day_index.find(f[3]);
    if (day_it == day_index.end()) {
      ++unmapped_rows;
      continue;
    }
    const auto factor_it = factors.find(f[3]);
    if (factor_it == factors.end()) {
      ++unmapped_rows;
      continue;
    }
    Row row;
    row.stock = stock;
    row.date = f[3];
    row.day = day_it->second;
    row.ret = number(f[4]);
    row.rf = number(f[6]);
    const Factor& factor = factor_it->second;
    row.x = {1.0, factor.rmrf, factor.smb, factor.hml};
    row.valid_return = std::isfinite(row.ret) && std::isfinite(row.rf);
    group.push_back(std::move(row));
    ++input_rows;
  }
  write_group(group, output, labels, targets);
  output.close();
  std::cerr << "lines=" << input_lines << " rows=" << input_rows
            << " targets=" << targets << " labels=" << labels
            << " malformed=" << malformed_rows << " unmapped=" << unmapped_rows << '\n';
  return output ? 0 : 5;
}
