var default_branch;
function get_branches() {
  var elem = $("#gitbranch");
  elem.empty();
  var opt = $("<option></option>");
  opt.attr("value", "").text("Not applicable");
  elem.append(opt);
  var github_url = $("#giturl").val();
  if (!github_url) {
    return;
  }
  $.get(daGetGitBranches, { url: github_url }, "json").done(function (data) {
    //console.log(data);
    if (data.success) {
      var n = data.result.length;
      if (n > 0) {
        var default_to_use = default_branch;
        var to_try = [default_branch, daGithubBranch, "master", "main"];
        outer: for (var j = 0; j < 4; j++) {
          for (var i = 0; i < n; i++) {
            if (data.result[i].name == to_try[j]) {
              default_to_use = to_try[j];
              break outer;
            }
          }
        }
        elem.empty();
        for (var i = 0; i < n; i++) {
          opt = $("<option></option>");
          opt.attr("value", data.result[i].name).text(data.result[i].name);
          if (data.result[i].name == default_to_use) {
            opt.prop("selected", true);
          }
          $(elem).append(opt);
        }
      }
    }
  });
}
document.addEventListener("DOMContentLoaded", function () {
  $(document).ready(function () {
    default_branch = daDefaultBranch;
    get_branches();
    function sync_requirement() {
      // A Git URL install is named-only; the name is never inferred from the URL.
      var has_url = $.trim($("#giturl").val() || "") !== "";
      $("#requirement").prop("required", has_url);
    }
    sync_requirement();
    $("#giturl").on("change", function () {
      get_branches();
      sync_requirement();
    });
    $("#giturl").on("keyup", sync_requirement);
    $("form").on("submit", function (event) {
      if ($.trim($("#giturl").val() || "") !== "" && $.trim($("#requirement").val() || "") === "") {
        event.preventDefault();
        sync_requirement();
        $("#requirement").trigger("focus");
      }
    });
    $("#zipfile").on("change", function () {
      var fileName = $(this).val();
      fileName = fileName.replace(/.*\\/, "");
      fileName = fileName.replace(/.*\//, "");
      $(this).next(".custom-file-label").html(fileName);
    });
  });
});
